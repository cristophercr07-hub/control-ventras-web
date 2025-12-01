import os
import io
import datetime
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
    flash,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA Y SEGURA
# ---------------------------------------------------------

app = Flask(__name__)

# SECRET_KEY desde variable de entorno (seguro en Render)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")

# Soporte para DATABASE_URL de Render / Heroku
database_url = os.environ.get("DATABASE_URL") or "sqlite:///ventas.db"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Cookies de sesión más seguras
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"

# Debug controlado por variable de entorno
app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "0") == "1"

db = SQLAlchemy(app)

# Margen mínimo de utilidad para la calculadora (ahora sin límite: 0 %)
MIN_MARGIN_PERCENT = 0.0


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notes = db.Column(db.String(255))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pagado")

    # Datos comerciales
    name = db.Column(db.String(120), nullable=False)           # Cliente (texto libre)
    product = db.Column(db.String(120), nullable=False)        # Nombre de producto (texto)

    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)

    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)

    # Pagos
    payment_type = db.Column(db.String(50), default="Contado")  # Contado / Transferencia / Sinpe / etc.
    amount_paid = db.Column(db.Float, default=0.0)
    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date)

    notes = db.Column(db.String(255))  # Comentarios

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(20), nullable=False)  # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)


# ---------------------------------------------------------
# UTILIDADES / HELPERS
# ---------------------------------------------------------

def parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except Exception:
        return None


def current_user():
    """Devuelve el objeto User logueado o None."""
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            # No autorizado: redirigir a ventas (u otra pantalla)
            return redirect(url_for("ventas"))
        return f(*args, **kwargs)
    return wrapper


def query_for(model):
    """
    Devuelve un query filtrado por usuario.
    - Admin: ve todo.
    - Usuario normal: solo sus propios registros.
    """
    user = current_user()
    if not user:
        # Sin usuario, no debería ocurrir con login_required
        return model.query.filter(False)

    if user.is_admin:
        return model.query

    # Usuario normal
    return model.query.filter_by(user_id=user.id)


# ---------------------------------------------------------
# FILTROS Y CONTEXTO JINJA
# ---------------------------------------------------------

@app.template_filter("format_num")
def format_num_filter(value):
    """Formatea números para mostrarlos como montos en CRC."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    # Miles con punto, sin decimales
    s = f"{n:,.0f}"
    return s.replace(",", ".")


@app.template_filter("zip")
def zip_filter(a, b):
    """Permite usar week_labels|zip(week_values) si quedara en alguna plantilla."""
    return zip(a or [], b or [])


@app.context_processor
def inject_globals():
    from datetime import date as _date
    return dict(
        current_user=current_user,
        date=_date,  # para plantillas que usaban date()
    )


# ---------------------------------------------------------
# INICIALIZACIÓN DE LA BD Y USUARIO ADMIN
# ---------------------------------------------------------

with app.app_context():
    db.create_all()
    # Crear usuario admin por defecto si no existe
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin"),  # cámbialo en producción
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
        if user and user.check_password(password):
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
# GESTIÓN DE USUARIOS (SOLO ADMIN)
# ---------------------------------------------------------

@app.route("/usuarios", methods=["GET", "POST"])
@login_required
@admin_required
def usuarios():
    error = None
    success = None

    if request.method == "POST":
        action = request.form.get("action") or "create"

        # Crear usuario
        if action == "create":
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

        # Cambiar contraseña de un usuario (desde dentro, solo admin)
        elif action == "change_password":
            user_id = request.form.get("user_id")
            new_password = request.form.get("new_password") or ""
            if not user_id or not new_password:
                error = "Debes seleccionar un usuario y una nueva contraseña."
            else:
                u = User.query.get(int(user_id))
                if not u:
                    error = "Usuario no encontrado."
                else:
                    u.password_hash = generate_password_hash(new_password)
                    db.session.commit()
                    success = f"Contraseña actualizada para {u.username}."

    users = User.query.order_by(User.username).all()
    return render_template(
        "usuarios.html",
        users=users,
        error=error,
        success=success,
    )


# ---------------------------------------------------------
# RUTA RAÍZ
# ---------------------------------------------------------

@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# CLIENTES
# ---------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    error = None
    success = None
    user = current_user()

    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            notes = (request.form.get("notes") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")

            c = Client(
                user_id=user.id,
                name=name,
                phone=phone,
                email=email,
                notes=notes,
            )
            db.session.add(c)
            db.session.commit()
            success = "Cliente creado correctamente."
        except Exception as e:
            error = f"Error al guardar el cliente: {e}"

    # Admin ve todos, usuario solo los suyos (query_for se encarga)
    clients_query = query_for(Client)
    clients = clients_query.order_by(Client.name).all()

    return render_template(
        "clientes.html",
        clients=clients,
        error=error,
        success=success,
    )


@app.post("/clientes/<int:client_id>/delete")
@login_required
def delete_client(client_id):
    q = query_for(Client)
    client = q.filter_by(id=client_id).first_or_404()
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


# ---------------------------------------------------------
# PRODUCTOS + CALCULADORA
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    error = None
    success = None
    user = current_user()

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
                margin = float(margin_input or 0)  # sin límite mínimo
                quantity = int(quantity_input or 1)

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
                    existing = query_for(Product).filter_by(
                        name=product_name_input
                    ).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price_result
                        existing.margin_percent = margin_used
                    else:
                        p = Product(
                            user_id=user.id,
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

                existing = query_for(Product).filter_by(name=name).first()
                if existing:
                    existing.cost = cost
                    existing.price = price
                    existing.margin_percent = margin_percent
                    db.session.commit()
                    success = "Producto actualizado correctamente."
                else:
                    p = Product(
                        user_id=user.id,
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

    products = query_for(Product).order_by(Product.name).all()
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
@login_required
def delete_product(product_id):
    q = query_for(Product)
    product = q.filter_by(id=product_id).first_or_404()
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos"))


@app.route("/calculadora", methods=["GET", "POST"])
@login_required
def calculadora():
    # Siempre usamos la pantalla fusionada de productos + calculadora
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
@login_required
def ventas():
    error = None
    success = request.args.get("success")
    user = current_user()

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date_val = parse_date(date_str) or datetime.date.today()

            # Cliente
            client_id = request.form.get("client_id") or ""
            client_obj = None
            if client_id:
                client_obj = query_for(Client).filter_by(id=int(client_id)).first()

            name_text = (request.form.get("name") or "").strip()
            if client_obj:
                name = client_obj.name
            else:
                name = name_text

            product_from_select = (request.form.get("product_select") or "").strip()
            product_input = (request.form.get("product") or "").strip()
            product = product_input or product_from_select

            status = request.form.get("status") or "Pagado"
            payment_type = request.form.get("payment_type") or "Contado"

            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)
            quantity = int(request.form.get("quantity") or 1)

            amount_paid = float(request.form.get("amount_paid") or 0)
            due_date_str = request.form.get("due_date") or ""
            due_date = parse_date(due_date_str)
            notes = (request.form.get("notes") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio (o selecciona un cliente).")
            if not product:
                raise ValueError("Debes seleccionar o escribir un producto.")
            if quantity <= 0:
                raise ValueError("La cantidad debe ser mayor que cero.")

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity
            pending_amount = max(total - amount_paid, 0.0)

            sale = Sale(
                user_id=user.id,
                date=date_val,
                name=name,
                product=product,
                status=status,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                quantity=quantity,
                total=total,
                profit=profit,
                payment_type=payment_type,
                amount_paid=amount_paid,
                pending_amount=pending_amount,
                due_date=due_date,
                notes=notes,
                client_id=client_obj.id if client_obj else None,
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

    query = query_for(Sale)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    # Totales
    total_ventas = len(sales)
    total_monto = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_pagado = sum(float(s.amount_paid or 0) for s in sales)
    total_pendiente = sum(float(s.pending_amount or 0) for s in sales)

    products = query_for(Product).order_by(Product.name).all()
    clients = query_for(Client).order_by(Client.name).all()

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
@login_required
def delete_sale(sale_id):
    q = query_for(Sale)
    sale = q.filter_by(id=sale_id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas", success="Venta eliminada correctamente."))


@app.route("/ventas/export")
@login_required
def ventas_export():
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    query = query_for(Sale)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.asc(), Sale.id.asc()).all()

    rows = []
    for s in sales:
        rows.append({
            "Fecha": s.date.isoformat() if s.date else "",
            "Nombre / Cliente": s.name,
            "Producto": s.product,
            "Estado": s.status,
            "Tipo de pago": s.payment_type,
            "Costo por unidad": s.cost_per_unit,
            "Precio por unidad": s.price_per_unit,
            "Cantidad": s.quantity,
            "Total": s.total,
            "Pagado": s.amount_paid,
            "Pendiente": s.pending_amount,
            "Fecha vencimiento": s.due_date.isoformat() if s.due_date else "",
            "Ganancia": s.profit,
            "Comentario": s.notes or "",
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
# CONTROL DE FLUJO (GASTOS / REINVERSIÓN / AHORRO)
# ---------------------------------------------------------

@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
    error = None
    success = None
    user = current_user()

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date_val = parse_date(date_str) or datetime.date.today()
            description = (request.form.get("description") or "").strip()
            category = request.form.get("category") or "Gasto"
            amount = float(request.form.get("amount") or 0)

            if not description:
                raise ValueError("La descripción es obligatoria.")
            if amount <= 0:
                raise ValueError("El monto debe ser mayor que cero.")

            e = Expense(
                user_id=user.id,
                date=date_val,
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

    exp_query = query_for(Expense)
    sales_query = query_for(Sale)

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
@login_required
def flujo_export():
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = query_for(Expense)

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
@login_required
def dashboard():
    # Filtros de fecha + presets rápidos
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    preset = request.args.get("preset") or ""

    sales_query = query_for(Sale)

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
        elif preset == "month":         # este mes
            d_to = today
            d_from = today.replace(day=1)
        elif preset == "year":          # este año
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

    # Top productos por ganancia acumulada
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

    # Totales de dashboard
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_monto_period = sum(float(s.total or 0) for s in sales)
    num_ventas = len(sales)
    avg_ticket = total_monto_period / num_ventas if num_ventas > 0 else 0.0

    # Ganancia diaria promedio
    if d_from and d_to and d_to >= d_from:
        days = (d_to - d_from).days + 1
    else:
        days = 0
    avg_daily_profit = total_ganancia / days if days > 0 else 0.0

    # Pagos vencidos / próximos sobre las ventas filtradas
    today = datetime.date.today()
    overdue_sales = [s for s in sales if s.pending_amount and s.pending_amount > 0 and s.due_date and s.due_date < today]
    upcoming_sales = [s for s in sales if s.pending_amount and s.pending_amount > 0 and s.due_date and s.due_date >= today]

    overdue_total = sum(float(s.pending_amount or 0) for s in overdue_sales)
    overdue_count = len(overdue_sales)

    upcoming_total = sum(float(s.pending_amount or 0) for s in upcoming_sales)
    upcoming_count = len(upcoming_sales)

    # Alertas simples
    alerts = []
    if overdue_total > 0:
        alerts.append({
            "level": "danger",
            "title": "Pagos vencidos",
            "message": f"Tienes ₡{format_num_filter(overdue_total)} pendientes de cobro en {overdue_count} venta(s).",
        })
    if upcoming_total > 0 and upcoming_count > 0:
        alerts.append({
            "level": "warning",
            "title": "Pagos próximos",
            "message": f"Hay ₡{format_num_filter(upcoming_total)} por cobrar en {upcoming_count} venta(s) próximas.",
        })

    return render_template(
        "dashboard.html",
        top_labels=top_labels,
        top_values=top_values,
        week_labels=week_labels,
        week_values=week_values,
        total_ganancia=total_ganancia,
        total_monto_period=total_monto_period,
        avg_ticket=avg_ticket,
        avg_daily_profit=avg_daily_profit,
        overdue_total=overdue_total,
        overdue_count=overdue_count,
        upcoming_total=upcoming_total,
        upcoming_count=upcoming_count,
        alerts=alerts,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------
# MAIN (para ejecución local)
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
