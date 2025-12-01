import datetime
import io
from collections import defaultdict
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_file,
    jsonify,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# ---------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = "cambia-esta-clave-a-una-muy-segura"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///ventas.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Margen mínimo de utilidad para la calculadora (0% = sin límite)
MIN_MARGIN_PERCENT = 0.0


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notes = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    name = db.Column(db.String(120), nullable=False)      # Nombre / cliente (texto plano)
    product = db.Column(db.String(120), nullable=False)   # Nombre producto (texto plano)
    status = db.Column(db.String(20), nullable=False)     # Pagado / Pendiente
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)
    comment = db.Column(db.String(255))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(20), nullable=False)   # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


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


def format_num(value):
    """
    Formato numérico amigable para LATAM:
    12345.6 -> 12.345,60
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    s = f"{number:,.2f}"  # 12,345.60
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


@app.context_processor
def inject_template_helpers():
    return {
        "current_user": get_current_user,
        "format_num": format_num,
    }


@app.template_filter("zip")
def zip_filter(a, b):
    """
    Permite usar: {% for x, y in lista1|zip(lista2) %}
    """
    try:
        return zip(a, b)
    except TypeError:
        return []


def require_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------
# INICIALIZACIÓN DE LA BD Y USUARIO ADMIN
# ---------------------------------------------------------

@app.before_first_request
def init_db():
    db.create_all()
    # Crea usuario admin por defecto si no existe
    if not User.query.filter_by(username="admin").first():
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
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["user"] = user.username
            session["is_admin"] = bool(user.is_admin)
            return redirect(url_for("dashboard"))
        else:
            error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------
# RUTA RAÍZ
# ---------------------------------------------------------

@app.route("/")
@require_login
def index():
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# GESTIÓN DE USUARIOS (SOLO ADMIN)
# ---------------------------------------------------------

@app.route("/usuarios", methods=["GET", "POST"])
@require_login
@require_admin
def usuarios():
    error = None
    success = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            error = "Usuario y contraseña son obligatorios."
        else:
            existing = User.query.filter_by(username=username).first()
            if existing:
                error = "Ya existe un usuario con ese nombre."
            else:
                new_user = User(
                    username=username,
                    password_hash=generate_password_hash(password),
                    is_admin=is_admin,
                )
                db.session.add(new_user)
                db.session.commit()
                success = "Usuario creado correctamente."

    users = User.query.order_by(User.username).all()
    return render_template(
        "usuarios.html",
        users=users,
        error=error,
        success=success,
    )


@app.post("/usuarios/<int:user_id>/delete")
@require_login
@require_admin
def delete_user(user_id):
    """
    Elimina un usuario (solo admin).
    No permite que el usuario actual se elimine a sí mismo.
    """
    user = User.query.get_or_404(user_id)

    # Evitar borrarse a sí mismo
    current = get_current_user()
    if current and current.id == user.id:
        return redirect(url_for("usuarios"))

    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("usuarios"))


# ---------------------------------------------------------
# CLIENTES
# ---------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
@require_login
def clientes():
    error = None
    success = None
    current = get_current_user()

    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            notes = (request.form.get("notes") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")

            c = Client(
                name=name,
                phone=phone,
                email=email,
                notes=notes,
                user_id=current.id,
            )
            db.session.add(c)
            db.session.commit()
            success = "Cliente creado correctamente."
        except Exception as e:
            error = f"Error al guardar el cliente: {e}"

    clients = Client.query.filter_by(user_id=current.id).order_by(Client.name).all()
    return render_template(
        "clientes.html",
        clients=clients,
        error=error,
        success=success,
    )


@app.post("/clientes/<int:client_id>/delete")
@require_login
def delete_client(client_id):
    current = get_current_user()
    client = Client.query.filter_by(id=client_id, user_id=current.id).first_or_404()
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


# ---------------------------------------------------------
# ENDPOINT OPCIONES PARA FORMULARIO DE VENTAS (ATALLO)
# ---------------------------------------------------------

@app.get("/api/opciones_venta")
@require_login
def opciones_venta():
    current = get_current_user()
    clients = Client.query.filter_by(user_id=current.id).order_by(Client.name).all()
    products = Product.query.filter_by(user_id=current.id).order_by(Product.name).all()

    return jsonify(
        {
            "clients": [
                {"id": c.id, "name": c.name, "phone": c.phone, "email": c.email}
                for c in clients
            ],
            "products": [
                {
                    "id": p.id,
                    "name": p.name,
                    "cost": p.cost,
                    "price": p.price,
                }
                for p in products
            ],
        }
    )


# ---------------------------------------------------------
# PRODUCTOS + CALCULADORA
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
@require_login
def productos():
    error = None
    success = None
    current = get_current_user()

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

                # Ya no hay margen mínimo rígido, se permite 0 o negativo
                if cost <= 0:
                    raise ValueError("El costo debe ser mayor que cero.")
                if quantity <= 0:
                    raise ValueError("La cantidad debe ser mayor que cero.")

                price_result = cost * (1 + margin / 100.0)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity
                margin_used = (profit_unit / cost * 100.0) if cost != 0 else 0.0

                if save_to_catalog:
                    if not product_name_input:
                        raise ValueError("Para guardar en el catálogo debes indicar un nombre de producto.")
                    existing = Product.query.filter_by(
                        name=product_name_input,
                        user_id=current.id,
                    ).first()
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
                            user_id=current.id,
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
                if cost > 0:
                    margin_percent = (price - cost) / cost * 100.0

                existing = Product.query.filter_by(name=name, user_id=current.id).first()
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
                        user_id=current.id,
                    )
                    db.session.add(p)
                    db.session.commit()
                    success = "Producto creado correctamente."
            except Exception as e:
                error = f"Error al guardar el producto: {e}"

    products = Product.query.filter_by(user_id=current.id).order_by(Product.name).all()
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
@require_login
@require_admin
def delete_product(product_id):
    current = get_current_user()
    product = Product.query.filter_by(id=product_id, user_id=current.id).first_or_404()
    db.session.delete(product)
    db.session.commit()
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
@require_login
def ventas():
    error = None
    success = request.args.get("success")
    current = get_current_user()

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date = parse_date(date_str) or datetime.date.today()

            name = (request.form.get("name") or "").strip()
            client_select = (request.form.get("client_select") or "").strip()
            product_from_select = (request.form.get("product_select") or "").strip()
            product_input = (request.form.get("product") or "").strip()
            product = product_input or product_from_select

            status = request.form.get("status") or "Pagado"
            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)
            quantity = int(request.form.get("quantity") or 1)
            comment = (request.form.get("comment") or "").strip()

            # Si se eligió un cliente del select y no se escribió manualmente, lo usamos
            if client_select and not name:
                name = client_select

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")
            if not product:
                raise ValueError("Debes seleccionar o escribir un producto.")
            if quantity <= 0:
                raise ValueError("La cantidad debe ser mayor que cero.")

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            # Resolver client_id si coincide por nombre (opcional)
            client_obj = None
            if client_select:
                client_obj = Client.query.filter_by(
                    name=client_select,
                    user_id=current.id,
                ).first()

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
                client_id=client_obj.id if client_obj else None,
                user_id=current.id,
            )
            db.session.add(sale)
            db.session.commit()
            success = "Venta guardada correctamente."
        except Exception as e:
            error = f"Error al guardar la venta: {e}"

    # Filtros para listado
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    query = Sale.query.filter_by(user_id=current.id)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    # Totales
    total_ventas = len(sales)
    total_monto = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_pagado = sum(float(s.total or 0) for s in sales if s.status == "Pagado")
    total_pendiente = sum(float(s.total or 0) for s in sales if s.status == "Pendiente")

    products = Product.query.filter_by(user_id=current.id).order_by(Product.name).all()
    clients = Client.query.filter_by(user_id=current.id).order_by(Client.name).all()

    return render_template(
        "ventas.html",
        error=error,
        success=success,
        sales=sales,
        products=products,
        clients=clients,
        filter_name=filter_name,
        filter_status=filter_status,
        date_from=date_from,
        date_to=date_to,
        total_ventas=total_ventas,
        total_monto=total_monto,
        total_ganancia=total_ganancia,
        total_pagado=total_pagado,
        total_pendiente=total_pendiente,
    )


@app.post("/ventas/<int:sale_id>/delete")
@require_login
def delete_sale(sale_id):
    current = get_current_user()
    sale = Sale.query.filter_by(id=sale_id, user_id=current.id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas", success="Venta eliminada correctamente."))


@app.route("/ventas/export")
@require_login
def ventas_export():
    current = get_current_user()

    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    query = Sale.query.filter_by(user_id=current.id)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
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
@require_login
def flujo():
    error = None
    success = None
    current = get_current_user()

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
                user_id=current.id,
            )
            db.session.add(e)
            db.session.commit()
            success = "Movimiento registrado correctamente."
        except Exception as e:
            error = f"Error al registrar movimiento: {e}"

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = Expense.query.filter_by(user_id=current.id)
    sales_query = Sale.query.filter_by(user_id=current.id)

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
@require_login
def flujo_export():
    current = get_current_user()

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = Expense.query.filter_by(user_id=current.id)

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
# DASHBOARD
# ---------------------------------------------------------

@app.route("/dashboard")
@require_login
def dashboard():
    current = get_current_user()

    # Filtros de fecha + presets rápidos
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    preset = request.args.get("preset") or ""

    sales_query = Sale.query.filter_by(user_id=current.id)

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

    # Top productos por ganancia acumulada (filtrada)
    profit_by_product = defaultdict(float)
    for s in sales:
        profit_by_product[s.product] += float(s.profit or 0)

    items = sorted(profit_by_product.items(), key=lambda x: x[1], reverse=True)
    top_items = items[:5]
    top_labels = [name for name, _ in top_items]
    top_values = [round(value, 2) for _, value in top_items]

    # Ganancias por semana (ISO week)
    profit_by_week = defaultdict(float)
    for s in sales:
        if not s.date:
            continue
        d = s.date
        if isinstance(d, datetime.datetime):
            d = d.date()
        y, w, _ = d.isocalendar()
        key = f"{y}-W{w:02d}"
        profit_by_week[key] += float(s.profit or 0)

    weeks_sorted = sorted(profit_by_week.items(), key=lambda x: x[0])
    week_labels = [k for k, _ in weeks_sorted]
    week_values = [round(v, 2) for _, v in weeks_sorted]

    total_ganancia = sum(float(s.profit or 0) for s in sales)

    return render_template(
        "dashboard.html",
        top_labels=top_labels,
        top_values=top_values,
        week_labels=week_labels,
        week_values=week_values,
        total_ganancia=total_ganancia,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
