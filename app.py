import datetime
import io
import os
from collections import defaultdict

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_file,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from sqlalchemy import inspect

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# ---------------------------------------------------------

app = Flask(__name__)

# SECRET_KEY desde variable de entorno (más seguro en producción)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-me")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Cookies de sesión más seguras
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# --------- CONFIG BD: Postgres (si hay DATABASE_URL) o SQLite local ---------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sqlite_path = os.path.join(BASE_DIR, "ventas.db")

db_url = os.environ.get("DATABASE_URL")

if db_url:
    # Render y otros servicios a veces dan 'postgres://', SQLAlchemy espera 'postgresql://'
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
else:
    # Respaldo local: SQLite
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{sqlite_path}"

db = SQLAlchemy(app)

# Margen mínimo de utilidad para la calculadora (7 %)
MIN_MARGIN_PERCENT = 7.0


# ---------------------------------------------------------
# FILTROS JINJA
# ---------------------------------------------------------

@app.template_filter("format_num")
def format_num_filter(value):
    """
    Formatea números con miles usando punto y decimales con coma:
    1234567.89 -> '1.234.567,89'
    """
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return "0,00"

    s = f"{num:,.2f}"  # Ej: '1,234,567.89'
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    name = db.Column(db.String(120), nullable=False)      # cliente
    product = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False)     # Pagado / Pendiente
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)
    comment = db.Column(db.String(255))

    # Usuario del sistema que registró la venta
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user = db.relationship("User", backref="sales", lazy=True)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(20), nullable=False)   # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)


# ---------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------

def parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except Exception:
        return None


def require_login():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return None


# ---------------------------------------------------------
# INICIALIZACIÓN DE LA BD, COLUMNA user_id EN SALE Y USUARIO ADMIN
# ---------------------------------------------------------

with app.app_context():
    # Crea tablas si no existen (en la BD que esté configurada)
    db.create_all()

    # Intentar asegurar que exista la columna user_id en tabla sale
    try:
        inspector = inspect(db.engine)
        cols = [col["name"] for col in inspector.get_columns("sale")]
        if "user_id" not in cols:
            try:
                db.session.execute("ALTER TABLE sale ADD COLUMN user_id INTEGER")
                db.session.commit()
            except Exception:
                db.session.rollback()
    except Exception:
        # Si la tabla aún no existe o hay problema de inspección, ignoramos
        pass

    # Crear o actualizar usuario admin usando variables de entorno
    admin_username = os.environ.get("ADMIN_USER", "admin")
    admin_password = os.environ.get("ADMIN_PASS")  # si no está, se usa fallback (solo para desarrollo)

    admin_user = User.query.filter_by(username=admin_username).first()

    if admin_user:
        # Si hay password en entorno, sincronizamos la contraseña con esa
        if admin_password:
            admin_user.password_hash = generate_password_hash(admin_password)
            admin_user.is_admin = True
            db.session.commit()
    else:
        # No existe ese usuario admin, lo creamos
        if admin_password:
            # Producción: credenciales fuertes vía entorno
            admin = User(
                username=admin_username,
                password_hash=generate_password_hash(admin_password),
                is_admin=True,
            )
        else:
            # Fallback para desarrollo local: admin / admin
            admin = User(
                username="admin",
                password_hash=generate_password_hash("admin"),
                is_admin=True,
            )
        db.session.add(admin)
        db.session.commit()


# ---------------------------------------------------------
# AUTENTICACIÓN
# ---------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("ventas"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["user"] = user.username
            session["is_admin"] = bool(user.is_admin)
            return redirect(url_for("ventas"))
        else:
            error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------
# GESTIÓN DE USUARIOS (SOLO ADMIN)
# ---------------------------------------------------------

@app.route("/usuarios", methods=["GET", "POST"])
def usuarios():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return redirect(url_for("ventas"))

    # Mensajes desde URL (delete) + POST (create)
    error = request.args.get("error")
    success = request.args.get("success")

    if request.method == "POST":
        form_error = None
        form_success = None

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            form_error = "Usuario y contraseña son obligatorios."
        else:
            existing = User.query.filter_by(username=username).first()
            if existing:
                form_error = "Ya existe un usuario con ese nombre."
            else:
                new_user = User(
                    username=username,
                    password_hash=generate_password_hash(password),
                    is_admin=is_admin,
                )
                db.session.add(new_user)
                db.session.commit()
                form_success = "Usuario creado correctamente."

        if form_error:
            error = form_error
        if form_success:
            success = form_success

    users = User.query.order_by(User.username).all()
    return render_template(
        "usuarios.html",
        users=users,
        error=error,
        success=success,
    )


@app.post("/usuarios/<int:user_id>/delete")
def delete_user(user_id):
    """Eliminar usuario (solo admin). El admin no puede eliminarse a sí mismo."""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return redirect(url_for("ventas"))

    current_user_id = session.get("user_id")
    user = User.query.get_or_404(user_id)

    if user.id == current_user_id:
        return redirect(
            url_for(
                "usuarios",
                error="No puedes eliminar tu propio usuario mientras estás conectado."
            )
        )

    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("usuarios", success="Usuario eliminado correctamente."))


# ---------------------------------------------------------
# RUTA RAÍZ
# ---------------------------------------------------------

@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("ventas"))


# ---------------------------------------------------------
# PRODUCTOS + CALCULADORA (FUSIONADOS)
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
def productos():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    error = None
    success = None

    # Valores por defecto para la calculadora
    product_name_input = ""
    cost_input = ""
    margin_input = f"{MIN_MARGIN_PERCENT:.2f}"
    quantity_input = "1"
    price_result = None
    profit_unit = None
    profit_total = None
    margin_used = None

    if request.method == "POST":
        form_type = (request.form.get("form_type") or "calculator").strip()

        # --- LÓGICA DE CALCULADORA ---
        if form_type == "calculator":
            try:
                product_name_input = (request.form.get("product_name") or "").strip()
                cost_input = request.form.get("cost") or "0"
                margin_input = request.form.get("margin") or f"{MIN_MARGIN_PERCENT:.2f}"
                quantity_input = request.form.get("quantity") or "1"
                save_to_catalog = bool(request.form.get("save_to_catalog"))

                cost = float(cost_input or 0)
                margin = float(margin_input or 0)
                quantity = int(quantity_input or 1)

                if margin < MIN_MARGIN_PERCENT:
                    margin = MIN_MARGIN_PERCENT

                if cost <= 0:
                    raise ValueError("El costo debe ser mayor que cero.")
                if quantity <= 0:
                    raise ValueError("La cantidad debe ser mayor que cero.")

                price_result = cost * (1 + margin / 100.0)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity
                margin_used = (profit_unit / cost * 100.0) if cost > 0 else 0.0

                if save_to_catalog:
                    if not product_name_input:
                        raise ValueError("Para guardar en el catálogo debes indicar un nombre de producto.")
                    existing = Product.query.filter_by(name=product_name_input).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price_result
                        existing.margin_percent = margin_used
                    else:
                        p = Product(
                            name=product_name_input,
                            cost=cost,
                            price=price_result,
                            margin_percent=margin_used,
                        )
                        db.session.add(p)
                    db.session.commit()
                    success = "Producto guardado/actualizado en el catálogo."
            except Exception as e:
                error = f"Error en la calculadora: {e}"

        # --- LÓGICA DE FORMULARIO DIRECTO DE CATÁLOGO ---
        elif form_type == "catalog":
            try:
                name = (request.form.get("name") or "").strip()
                cost = float(request.form.get("cost") or 0)
                price = float(request.form.get("price") or 0)

                if not name:
                    raise ValueError("El nombre del producto es obligatorio.")

                margin_percent = 0.0
                if cost > 0 and price >= cost:
                    margin_percent = (price - cost) / cost * 100.0

                existing = Product.query.filter_by(name=name).first()
                if existing:
                    existing.cost = cost
                    existing.price = price
                    existing.margin_percent = margin_percent
                    db.session.commit()
                    success = "Producto actualizado correctamente."
                else:
                    p = Product(
                        name=name,
                        cost=cost,
                        price=price,
                        margin_percent=margin_percent,
                    )
                    db.session.add(p)
                    db.session.commit()
                    success = "Producto creado correctamente."
            except Exception as e:
                error = f"Error al guardar el producto: {e}"

    products = Product.query.order_by(Product.name).all()
    return render_template(
        "productos.html",
        products=products,
        error=error,
        success=success,
        min_margin=MIN_MARGIN_PERCENT,
        product_name_input=product_name_input,
        cost_input=cost_input,
        margin_input=margin_input,
        quantity_input=quantity_input,
        price_result=price_result,
        profit_unit=profit_unit,
        profit_total=profit_total,
        margin_used=margin_used,
    )


@app.post("/productos/<int:product_id>/delete")
def delete_product(product_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return redirect(url_for("productos"))

    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos"))


# ---------------------------------------------------------
# REDIRECCIÓN /CALCULADORA -> /PRODUCTOS
# ---------------------------------------------------------

@app.route("/calculadora", methods=["GET", "POST"])
def calculadora():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("productos"))


# ---------------------------------------------------------
# VENTAS
# ---------------------------------------------------------

def apply_sales_filters(query, filter_name, filter_status, date_from_str, date_to_str):
    if filter_name:
        like_pattern = f"%{filter_name}%"
        query = query.filter(Sale.name.ilike(like_pattern))

    if filter_status:
        query = query.filter(Sale.status == filter_status)

    date_from = parse_date(date_from_str)
    date_to = parse_date(date_to_str)
    if date_from:
        query = query.filter(Sale.date >= date_from)
    if date_to:
        query = query.filter(Sale.date <= date_to)

    return query


@app.route("/ventas", methods=["GET", "POST"])
def ventas():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date = parse_date(date_str) or datetime.date.today()

            name = (request.form.get("name") or "").strip()
            product_from_select = (request.form.get("product_select") or "").strip()
            product_input = (request.form.get("product") or "").strip()
            product = product_input or product_from_select

            status = request.form.get("status") or "Pagado"
            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)
            quantity = int(request.form.get("quantity") or 1)
            comment = (request.form.get("comment") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")
            if not product:
                raise ValueError("Debes seleccionar o escribir un producto.")
            if quantity <= 0:
                raise ValueError("La cantidad debe ser mayor que cero.")

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            sale = Sale(
                date=date,
                name=name,
                product=product,
                status=status,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                quantity=quantity,
                total=total,
                profit=profit,
                comment=comment,
                user_id=session.get("user_id"),
            )
            db.session.add(sale)
            db.session.commit()
            success = "Venta guardada correctamente."
        except Exception as e:
            error = f"Error al guardar la venta: {e}"

    # Filtros (GET)
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    filter_user_id = request.args.get("filter_user_id") or ""

    query = Sale.query
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)

    # Filtro por usuario que registró la venta
    if filter_user_id:
        try:
            user_filter_id = int(filter_user_id)
            query = query.filter(Sale.user_id == user_filter_id)
        except ValueError:
            filter_user_id = ""

    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    # Totales
    total_ventas = len(sales)
    total_monto = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_pagado = sum(float(s.total or 0) for s in sales if s.status == "Pagado")
    total_pendiente = sum(float(s.total or 0) for s in sales if s.status == "Pendiente")

    products = Product.query.order_by(Product.name).all()
    users = User.query.order_by(User.username).all()

    # Diccionario para JS { nombre: {cost, price} }
    product_mapping = {
        p.name: {"cost": float(p.cost or 0), "price": float(p.price or 0)}
        for p in products
    }

    return render_template(
        "ventas.html",
        error=error,
        success=success,
        sales=sales,
        products=products,
        users=users,
        filter_name=filter_name,
        filter_status=filter_status,
        date_from=date_from,
        date_to=date_to,
        filter_user_id=filter_user_id,
        total_ventas=total_ventas,
        total_monto=total_monto,
        total_ganancia=total_ganancia,
        total_pagado=total_pagado,
        total_pendiente=total_pendiente,
        product_mapping=product_mapping,
    )


@app.post("/ventas/<int:sale_id>/delete")
def delete_sale(sale_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    sale = Sale.query.get_or_404(sale_id)
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas", success="Venta eliminada correctamente."))


@app.route("/ventas/export")
def ventas_export():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    filter_user_id = request.args.get("filter_user_id") or ""

    query = Sale.query
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)

    if filter_user_id:
        try:
            user_filter_id = int(filter_user_id)
            query = query.filter(Sale.user_id == user_filter_id)
        except ValueError:
            pass

    sales = query.order_by(Sale.date.asc(), Sale.id.asc()).all()

    rows = []
    for s in sales:
        rows.append({
            "Fecha": s.date.isoformat() if s.date else "",
            "Nombre / Cliente": s.name,
            "Producto": s.product,
            "Estado": s.status,
            "Costo por unidad": s.cost_per_unit,
            "Precio por unidad": s.price_per_unit,
            "Cantidad": s.quantity,
            "Total": s.total,
            "Ganancia": s.profit,
            "Comentario": s.comment or "",
            "Usuario": s.user.username if getattr(s, "user", None) else "",
        })

    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Ventas")
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="ventas_filtradas.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------
# CONTROL DE FLUJO (GASTOS / REINVERSIÓN)
# ---------------------------------------------------------

@app.route("/flujo", methods=["GET", "POST"])
def flujo():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    error = None
    success = None

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date = parse_date(date_str) or datetime.date.today()
            description = (request.form.get("description") or "").strip()
            category = request.form.get("category") or "Gasto"
            amount = float(request.form.get("amount") or 0)

            if not description:
                raise ValueError("La descripción es obligatoria.")
            if amount <= 0:
                raise ValueError("El monto debe ser mayor que cero.")

            e = Expense(
                date=date,
                description=description,
                category=category,
                amount=amount,
            )
            db.session.add(e)
            db.session.commit()
            success = "Movimiento registrado correctamente."
        except Exception as e:
            error = f"Error al registrar movimiento: {e}"

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = Expense.query
    sales_query = Sale.query

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    if d_from:
        exp_query = exp_query.filter(Expense.date >= d_from)
        sales_query = sales_query.filter(Sale.date >= d_from)
    if d_to:
        exp_query = exp_query.filter(Expense.date <= d_to)
        sales_query = sales_query.filter(Sale.date <= d_to)

    if category_filter:
        exp_query = exp_query.filter(Expense.category == category_filter)

    expenses = exp_query.order_by(Expense.date.desc(), Expense.id.desc()).all()
    sales = sales_query.all()

    total_ingresos = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)

    total_gastos = sum(float(e.amount or 0) for e in expenses if e.category == "Gasto")
    total_reinv = sum(float(e.amount or 0) for e in expenses if e.category == "Reinversión")

    total_egresos = total_gastos + total_reinv
    neto = total_ganancia - total_egresos

    ahorro_objetivo = total_ganancia * 0.10
    ahorro_real = max(neto, 0.0)
    ahorro_faltante = max(ahorro_objetivo - ahorro_real, 0.0)
    meta_cumplida = (total_ganancia > 0) and (ahorro_real >= ahorro_objetivo)

    return render_template(
        "flujo.html",
        error=error,
        success=success,
        expenses=expenses,
        date_from=date_from,
        date_to=date_to,
        category_filter=category_filter,
        total_ingresos=total_ingresos,
        total_ganancia=total_ganancia,
        total_gastos=total_gastos,
        total_reinv=total_reinv,
        total_egresos=total_egresos,
        neto=neto,
        ahorro_objetivo=ahorro_objetivo,
        ahorro_real=ahorro_real,
        ahorro_faltante=ahorro_faltante,
        meta_cumplida=meta_cumplida,
    )


@app.route("/flujo/export")
def flujo_export():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = Expense.query

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    if d_from:
        exp_query = exp_query.filter(Expense.date >= d_from)
    if d_to:
        exp_query = exp_query.filter(Expense.date <= d_to)
    if category_filter:
        exp_query = exp_query.filter(Expense.category == category_filter)

    expenses = exp_query.order_by(Expense.date.asc(), Expense.id.asc()).all()

    rows = []
    for e in expenses:
        rows.append({
            "Fecha": e.date.isoformat() if e.date else "",
            "Descripción": e.description,
            "Tipo": e.category,
            "Monto": e.amount,
        })

    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Flujo")
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="flujo_filtrado.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------
# DASHBOARD (ROBUSTO + ALERTAS)
# ---------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    # Filtros de fecha + presets rápidos
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    preset = request.args.get("preset") or ""

    sales_query = Sale.query

    d_from = None
    d_to = None

    if preset:
        today = datetime.date.today()

        if preset == "week":            # últimos 7 días
            d_to = today
            d_from = today - datetime.timedelta(days=7)
        elif preset == "4weeks":        # últimas 4 semanas (28 días)
            d_to = today
            d_from = today - datetime.timedelta(days=28)
        elif preset == "month":         # este mes (desde el día 1)
            d_to = today
            d_from = today.replace(day=1)
        elif preset == "year":          # este año (desde 1 de enero)
            d_to = today
            d_from = today.replace(month=1, day=1)

        date_from = d_from.isoformat() if d_from else ""
        date_to = d_to.isoformat() if d_to else ""
    else:
        d_from = parse_date(date_from)
        d_to = parse_date(date_to)

    if d_from:
        sales_query = sales_query.filter(Sale.date >= d_from)
    if d_to:
        sales_query = sales_query.filter(Sale.date <= d_to)

    sales = sales_query.order_by(Sale.date).all()

    # Métricas del periodo filtrado
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_monto_period = sum(float(s.total or 0) for s in sales)
    total_ventas_period = len(sales)

    avg_ticket = total_monto_period / total_ventas_period if total_ventas_period > 0 else 0.0
    avg_profit_per_sale = total_ganancia / total_ventas_period if total_ventas_period > 0 else 0.0

    # Promedio diario de utilidad (manejo robusto de fecha)
    profit_by_day = defaultdict(float)
    for s in sales:
        d = s.date
        if not d:
            continue
        if isinstance(d, datetime.datetime):
            d = d.date()
        if isinstance(d, str):
            try:
                d = datetime.date.fromisoformat(d)
            except Exception:
                continue
        profit_by_day[d] += float(s.profit or 0)

    num_dias = len(profit_by_day)
    avg_daily_profit = total_ganancia / num_dias if num_dias > 0 else 0.0

    # Top productos por ganancia acumulada (filtrada)
    profit_by_product = defaultdict(float)
    for s in sales:
        profit_by_product[s.product] += float(s.profit or 0)

    items = sorted(profit_by_product.items(), key=lambda x: x[1], reverse=True)
    top_items = items[:5]
    top_labels = [name for name, _ in top_items]
    top_values = [round(value, 2) for _, value in top_items]

    # Ganancias por semana (ISO week) con manejo robusto de fechas
    profit_by_week = defaultdict(float)
    for s in sales:
        d = s.date
        if not d:
            continue
        if isinstance(d, datetime.datetime):
            d = d.date()
        if isinstance(d, str):
            try:
                d = datetime.date.fromisoformat(d)
            except Exception:
                continue
        try:
            y, w, _ = d.isocalendar()
        except Exception:
            continue
        key = f"{y}-W{w:02d}"
        profit_by_week[key] += float(s.profit or 0)

    weeks_sorted = sorted(profit_by_week.items(), key=lambda x: x[0])
    week_labels = [k for k, _ in weeks_sorted]
    week_values = [round(v, 2) for _, v in weeks_sorted]

    # Ganancia por usuario del sistema
    profit_by_user = defaultdict(float)
    for s in sales:
        if s.user:
            profit_by_user[s.user.username] += float(s.profit or 0)

    user_items = sorted(profit_by_user.items(), key=lambda x: x[1], reverse=True)
    user_labels = [u for u, _ in user_items]
    user_values = [round(v, 2) for _, v in user_items]

    # -------------------------------------------------
    # ALERTAS AUTOMÁTICAS (a nivel global, no solo filtrado)
    # -------------------------------------------------
    alerts = []

    today = datetime.date.today()

    # 1) Ventas pendientes con más de 1 día de antigüedad (manejo robusto de fecha)
    pending_sales = Sale.query.filter(Sale.status == "Pendiente").all()
    old_pending = []
    for s in pending_sales:
        d = s.date
        if not d:
            continue
        if isinstance(d, datetime.datetime):
            d = d.date()
        if isinstance(d, str):
            try:
                d = datetime.date.fromisoformat(d)
            except Exception:
                continue
        if d <= today - datetime.timedelta(days=1):
            old_pending.append(s)

    if old_pending:
        total_pend_antiguo = sum(float(s.total or 0) for s in old_pending)
        alerts.append({
            "level": "warning",
            "title": "Ventas pendientes con antigüedad",
            "message": (
                f"Tienes {len(old_pending)} ventas pendientes con más de 1 día "
                f"por un monto total aproximado de ₡{total_pend_antiguo:,.2f}. "
                "Revisa los cobros para no perder liquidez."
            ),
        })

    # 2) Utilidad semanal por debajo de un umbral objetivo
    seven_days_ago = today - datetime.timedelta(days=7)
    try:
        weekly_sales = Sale.query.filter(
            Sale.date >= seven_days_ago,
            Sale.date <= today
        ).all()
        weekly_profit = sum(float(s.profit or 0) for s in weekly_sales)
    except Exception:
        weekly_profit = 0.0

    # Umbral configurable vía variable de entorno (opcional)
    min_weekly_profit_str = os.environ.get("ALERT_WEEKLY_PROFIT_MIN", "").strip()
    try:
        min_weekly_profit = float(min_weekly_profit_str) if min_weekly_profit_str else 0.0
    except ValueError:
        min_weekly_profit = 0.0

    if min_weekly_profit > 0 and weekly_profit < min_weekly_profit:
        alerts.append({
            "level": "danger",
            "title": "Utilidad semanal por debajo del objetivo",
            "message": (
                f"La utilidad de los últimos 7 días es de ₡{weekly_profit:,.2f}, "
                f"por debajo del objetivo mínimo de ₡{min_weekly_profit:,.2f}. "
                "Considera ajustar precios, volumen de ventas o estructura de gastos."
            ),
        })

    return render_template(
        "dashboard.html",
        top_labels=top_labels,
        top_values=top_values,
        week_labels=week_labels,
        week_values=week_values,
        user_labels=user_labels,
        user_values=user_values,
        total_ganancia=total_ganancia,
        date_from=date_from,
        date_to=date_to,
        # métricas nuevas:
        total_ventas_period=total_ventas_period,
        total_monto_period=total_monto_period,
        avg_ticket=avg_ticket,
        avg_profit_per_sale=avg_profit_per_sale,
        avg_daily_profit=avg_daily_profit,
        # alertas:
        alerts=alerts,
        weekly_profit=weekly_profit,
        min_weekly_profit=min_weekly_profit,
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":
    # En local, debug=True. En producción (Render) usas gunicorn, no este bloque.
    app.run(debug=True)
