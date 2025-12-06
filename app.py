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
    jsonify,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA Y SEGURA
# ---------------------------------------------------------

app = Flask(__name__)

# SECRET_KEY desde variable de entorno (seguro en producción)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambiar_esto_en_produccion")

# BASE DE DATOS: permite usar DATABASE_URL (Render, Heroku, etc.) o local SQLite
database_url = os.environ.get("DATABASE_URL") or "sqlite:///ventas.db"
# Ajuste para compatibilidad con SQLAlchemy
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Margen mínimo de utilidad para la calculadora
MIN_MARGIN_PERCENT = 0.0


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    def check_password(self, password_plain):
        return check_password_hash(self.password_hash, password_plain)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    notes = db.Column(db.String(255))

    user = db.relationship("User", backref=db.backref("clients", lazy=True))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)

    user = db.relationship("User", backref=db.backref("products", lazy=True))


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
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, default=0.0)

    user = db.relationship("User", backref=db.backref("expenses", lazy=True))


# ---------------------------------------------------------
# FILTROS JINJA
# ---------------------------------------------------------

@app.template_filter("format_num")
def format_num(value):
    """
    Formatea números con separador de miles y 2 decimales en formato latino.
    Ejemplo: 12345.6 -> '12.345,60'
    """
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return "0,00"
    s = f"{value:,.2f}"
    # 12,345.67 -> 12.345,67
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


@app.template_filter("zip")
def zip_filter(a, b):
    """
    Permite usar en templates:
    {% for x, y in lista1|zip(lista2) %}
    """
    try:
        return zip(a, b)
    except TypeError:
        return []


# ---------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------

def parse_date(date_str):
    if not date_str:
        return None
    try:
        year, month, day = map(int, date_str.split("-"))
        return datetime.date(year, month, day)
    except Exception:
        return None


def query_for(model):
    return db.session.query(model)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


# ---------------------------------------------------------
# AUTENTICACIÓN
# ---------------------------------------------------------

@app.route("/init_admin")
def init_admin():
    """
    Crea un usuario admin por defecto si no existe ninguno.
    """
    if User.query.first():
        return "Ya existe al menos un usuario."

    admin = User(
        username="admin",
        password_hash=generate_password_hash("admin"),
        is_admin=True,
    )
    db.session.add(admin)
    db.session.commit()
    return "Usuario admin creado: admin / admin"


@app.route("/reset_admin")
def reset_admin():
    """
    Fuerza la existencia de un usuario admin con contraseña admin/admin.
    """
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            is_admin=True,
        )
        db.session.add(admin)
    else:
        admin.is_admin = True
        admin.password_hash = generate_password_hash("admin")

    db.session.commit()
    return "Usuario admin reseteado: admin / admin"


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username") or ""
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            error = "Usuario o contraseña inválidos."
        else:
            session["user_id"] = user.id
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------
# APLICACIÓN PRINCIPAL
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


def get_default_date_range():
    today = datetime.date.today()
    first_day = today.replace(day=1)
    return first_day, today


@app.route("/")
@login_required
def dashboard():
    user = current_user()
    today = datetime.date.today()
    first_day, last_day = get_default_date_range()

    query = query_for(Sale).filter(Sale.user_id == user.id)
    query = query.filter(Sale.date >= first_day, Sale.date <= last_day)
    sales = query.order_by(Sale.date.asc()).all()

    expenses_q = query_for(Expense).filter(Expense.user_id == user.id)
    expenses_q = expenses_q.filter(Expense.date >= first_day, Expense.date <= last_day)
    expenses = expenses_q.order_by(Expense.date.asc()).all()

    total_sales = sum(float(s.total or 0) for s in sales)
    total_profit = sum(float(s.profit or 0) for s in sales)
    total_expenses = sum(float(e.amount or 0) for e in expenses)
    balance = total_profit - total_expenses

    daily_sales = defaultdict(float)
    daily_profit = defaultdict(float)
    daily_expenses = defaultdict(float)

    for s in sales:
        daily_sales[s.date] += float(s.total or 0)
        daily_profit[s.date] += float(s.profit or 0)

    for e in expenses:
        daily_expenses[e.date] += float(e.amount or 0)

    dates = sorted(set(list(daily_sales.keys()) + list(daily_expenses.keys())))
    chart_labels = [d.strftime("%d-%m") for d in dates]
    chart_sales = [round(daily_sales[d], 2) for d in dates]
    chart_profit = [round(daily_profit[d], 2) for d in dates]
    chart_expenses = [round(daily_expenses[d], 2) for d in dates]

    recent_sales = (
        query_for(Sale)
        .filter(Sale.user_id == user.id)
        .order_by(Sale.date.desc(), Sale.id.desc())
        .limit(10)
        .all()
    )

    # Para la parte semanal (si tu template la usa con week_labels, week_values)
    week_labels = []
    week_values = []

    return render_template(
        "dashboard.html",
        user=user,
        total_sales=total_sales,
        total_profit=total_profit,
        total_expenses=total_expenses,
        balance=balance,
        chart_labels=chart_labels,
        chart_sales=chart_sales,
        chart_profit=chart_profit,
        chart_expenses=chart_expenses,
        recent_sales=recent_sales,
        week_labels=week_labels,
        week_values=week_values,
    )


# ---------------------------------------------------------
# USUARIOS
# ---------------------------------------------------------

@app.route("/usuarios", methods=["GET", "POST"])
@login_required
def usuarios():
    user = current_user()
    if not user.is_admin:
        flash("No tienes permisos para administrar usuarios.", "danger")
        return redirect(url_for("dashboard"))

    error = None
    success = None

    if request.method == "POST":
        try:
            username = (request.form.get("username") or "").strip()
            password = (request.form.get("password") or "").strip()
            is_admin = bool(request.form.get("is_admin"))

            if not username or not password:
                raise ValueError("Usuario y contraseña son obligatorios.")

            existing = User.query.filter_by(username=username).first()
            if existing:
                raise ValueError("Ya existe un usuario con ese nombre.")

            new_user = User(
                username=username,
                password_hash=generate_password_hash(password),
                is_admin=is_admin,
            )
            db.session.add(new_user)
            db.session.commit()
            success = "Usuario creado correctamente."
        except Exception as e:
            error = str(e)

    users = User.query.order_by(User.id.asc()).all()
    return render_template("usuarios.html", users=users, error=error, success=success)


@app.post("/usuarios/<int:user_id>/delete")
@login_required
def delete_user(user_id):
    user = current_user()
    if not user.is_admin:
        flash("No tienes permisos para eliminar usuarios.", "danger")
        return redirect(url_for("usuarios"))

    if user.id == user_id:
        flash("No puedes eliminar tu propio usuario.", "danger")
        return redirect(url_for("usuarios"))

    u = User.query.get_or_404(user_id)
    db.session.delete(u)
    db.session.commit()
    flash("Usuario eliminado.", "success")
    return redirect(url_for("usuarios"))


# ---------------------------------------------------------
# CLIENTES
# ---------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    user = current_user()
    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            address = (request.form.get("address") or "").strip()
            notes = (request.form.get("notes") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")

            client = Client(
                user_id=user.id,
                name=name,
                phone=phone,
                email=email,
                address=address,
                notes=notes,
            )
            db.session.add(client)
            db.session.commit()
            success = "Cliente guardado correctamente."
        except Exception as e:
            error = str(e)

    filter_name = request.args.get("filter_name") or ""
    query = query_for(Client).filter(Client.user_id == user.id)
    if filter_name:
        like_pattern = f"%{filter_name}%"
        query = query.filter(Client.name.ilike(like_pattern))

    clients = query.order_by(Client.name.asc()).all()
    return render_template(
        "clientes.html",
        error=error,
        success=success,
        clients=clients,
        filter_name=filter_name,
    )


@app.post("/clientes/<int:client_id>/delete")
@login_required
def delete_client(client_id):
    user = current_user()
    client = Client.query.filter_by(id=client_id, user_id=user.id).first_or_404()
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes", success="Cliente eliminado correctamente."))


# ---------------------------------------------------------
# PRODUCTOS
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    user = current_user()
    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            cost = float(request.form.get("cost") or 0)
            price = float(request.form.get("price") or 0)

            if not name:
                raise ValueError("El nombre del producto es obligatorio.")
            if cost < 0 or price < 0:
                raise ValueError("Costos y precios no pueden ser negativos.")

            existing = (
                Product.query.filter_by(user_id=user.id, name=name).first()
            )
            if existing:
                raise ValueError("Ya existe un producto con ese nombre.")

            product = Product(
                user_id=user.id,
                name=name,
                description=description,
                cost=cost,
                price=price,
            )
            db.session.add(product)
            db.session.commit()
            success = "Producto agregado correctamente."
        except Exception as e:
            error = str(e)

    filter_name = request.args.get("filter_name") or ""
    query = query_for(Product).filter(Product.user_id == user.id)
    if filter_name:
        like_pattern = f"%{filter_name}%"
        query = query.filter(Product.name.ilike(like_pattern))

    products = query.order_by(Product.name.asc()).all()
    return render_template(
        "productos.html",
        error=error,
        success=success,
        products=products,
        filter_name=filter_name,
    )


@app.post("/productos/<int:product_id>/delete")
@login_required
def delete_product(product_id):
    user = current_user()
    product = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos", success="Producto eliminado correctamente."))


# ---------------------------------------------------------
# VENTAS
# ---------------------------------------------------------

@app.route("/ventas", methods=["GET", "POST"])
@login_required
def ventas():
    user = current_user()
    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date_val = parse_date(date_str) or datetime.date.today()

            # Cliente
            client_id = request.form.get("client_id") or ""
            client_obj = None
            if client_id:
                client_obj = (
                    query_for(Client)
                    .filter_by(id=int(client_id), user_id=user.id)
                    .first()
                )

            # Nombre del cliente (texto libre)
            name = (request.form.get("client_name") or "").strip()
            if client_obj and not name:
                name = client_obj.name

            product_from_select = request.form.get("product_from_select") or ""
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

            # NUEVA LÓGICA:
            # - Si la venta se marca como Pagado y no se indicó monto, asumimos que se pagó todo.
            # - Si es Pendiente, se calcula pendiente como total - amount_paid.
            if status == "Pagado":
                if amount_paid <= 0:
                    amount_paid = total
                pending_amount = 0.0
            else:
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

    query = query_for(Sale).filter(Sale.user_id == user.id)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    # Totales
    total_ventas = len(sales)
    total_monto = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_pagado = sum(float(s.amount_paid or 0) for s in sales)
    total_pendiente = sum(float(s.pending_amount or 0) for s in sales)

    products = (
        query_for(Product)
        .filter(Product.user_id == user.id)
        .order_by(Product.name.asc())
        .all()
    )
    clients = (
        query_for(Client)
        .filter(Client.user_id == user.id)
        .order_by(Client.name.asc())
        .all()
    )

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
    user = current_user()
    q = query_for(Sale).filter(Sale.user_id == user.id)
    sale = q.filter_by(id=sale_id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas", success="Venta eliminada correctamente."))


@app.post("/ventas/<int:sale_id>/update_amount_paid")
@login_required
def update_sale_amount_paid(sale_id):
    """
    NUEVA RUTA:
    Actualiza el monto pagado de una venta desde el listado y ajusta estado/pending_amount.
    """
    user = current_user()
    q = query_for(Sale).filter(Sale.user_id == user.id)
    sale = q.filter_by(id=sale_id).first_or_404()

    raw_amount = request.form.get("amount_paid") or "0"

    try:
        # Permite valores con coma o punto
        raw_amount = raw_amount.replace(",", ".")
        amount_paid = float(raw_amount)
    except ValueError:
        amount_paid = 0.0

    total = float(sale.total or 0.0)

    # Actualizamos montos
    sale.amount_paid = amount_paid
    sale.pending_amount = max(total - amount_paid, 0.0)

    # Si ya no hay saldo pendiente, marcamos como Pagado
    if sale.pending_amount <= 0.01:
        sale.status = "Pagado"
        sale.pending_amount = 0.0
    else:
        sale.status = "Pendiente"

    db.session.commit()
    return redirect(url_for("ventas", success="Monto pagado actualizado correctamente."))


@app.route("/ventas/export")
@login_required
def ventas_export():
    user = current_user()

    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    query = query_for(Sale).filter(Sale.user_id == user.id)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.asc(), Sale.id.asc()).all()

    output = io.StringIO()
    output.write(
        "Fecha,Cliente,Producto,Cantidad,Precio unidad,Total,Ganancia,Estado,Pagado,Pendiente,Tipo pago,Comentario\n"
    )
    for s in sales:
        output.write(
            f"{s.date},{s.name},{s.product},{s.quantity},"
            f"{s.price_per_unit},{s.total},{s.profit},{s.status},"
            f"{s.amount_paid},{s.pending_amount},{s.payment_type},{s.notes or ''}\n"
        )

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8"))
    mem.seek(0)
    filename = f"ventas_export_{datetime.date.today().isoformat()}.csv"
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv",
    )


# ---------------------------------------------------------
# FLUJO DE CAJA
# ---------------------------------------------------------

@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
    user = current_user()
    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date_val = parse_date(date_str) or datetime.date.today()

            description = (request.form.get("description") or "").strip()
            category = (request.form.get("category") or "").strip()
            amount = float(request.form.get("amount") or 0)

            if not description:
                raise ValueError("La descripción es obligatoria.")
            if not category:
                raise ValueError("La categoría es obligatoria.")
            if amount == 0:
                raise ValueError("El monto no puede ser cero.")

            expense = Expense(
                user_id=user.id,
                date=date_val,
                description=description,
                category=category,
                amount=amount,
            )
            db.session.add(expense)
            db.session.commit()
            success = "Movimiento registrado correctamente."
        except Exception as e:
            error = f"Error al guardar el movimiento: {e}"

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = query_for(Expense).filter(Expense.user_id == user.id)
    sales_query = query_for(Sale).filter(Sale.user_id == user.id)

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

    expenses = exp_query.order_by(Expense.date.asc()).all()
    sales = sales_query.order_by(Sale.date.asc()).all()

    total_expenses = sum(float(e.amount or 0) for e in expenses)
    total_sales = sum(float(s.total or 0) for s in sales)
    total_profit = sum(float(s.profit or 0) for s in sales)
    balance = total_profit - total_expenses

    category_totals = defaultdict(float)
    for e in expenses:
        category_totals[e.category] += float(e.amount or 0)

    category_labels = list(category_totals.keys())
    category_values = [round(category_totals[c], 2) for c in category_labels]

    return render_template(
        "flujo.html",
        error=error,
        success=success,
        expenses=expenses,
        total_expenses=total_expenses,
        total_sales=total_sales,
        total_profit=total_profit,
        balance=balance,
        date_from=date_from,
        date_to=date_to,
        category_filter=category_filter,
        category_labels=category_labels,
        category_values=category_values,
    )


@app.post("/flujo/<int:expense_id>/delete")
@login_required
def delete_expense(expense_id):
    user = current_user()
    e = Expense.query.filter_by(id=expense_id, user_id=user.id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    return redirect(url_for("flujo", success="Movimiento eliminado correctamente."))


# ---------------------------------------------------------
# CALCULADORA
# ---------------------------------------------------------

@app.route("/calculadora", methods=["GET", "POST"])
@login_required
def calculadora():
    user = current_user()
    error = None
    result = None

    if request.method == "POST":
        try:
            mode = request.form.get("mode") or "price_from_cost"

            cost = float(request.form.get("cost") or 0)
            margin = float(request.form.get("margin") or 0)
            price = float(request.form.get("price") or 0)
            quantity = int(request.form.get("quantity") or 1)
            product_name_input = (request.form.get("product_name") or "").strip()
            save_to_catalog = bool(request.form.get("save_to_catalog"))

            if mode == "price_from_cost":
                if cost <= 0:
                    raise ValueError("El costo debe ser mayor que cero.")
                if margin < MIN_MARGIN_PERCENT:
                    raise ValueError(f"El margen debe ser al menos {MIN_MARGIN_PERCENT:.2f} %.")
                if quantity <= 0:
                    raise ValueError("La cantidad debe ser mayor que cero.")

                price_result = cost * (1 + margin / 100.0)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity
                margin_used = (profit_unit / cost * 100.0) if cost > 0 else 0.0

                if save_to_catalog:
                    if not product_name_input:
                        raise ValueError("Para guardar en el catálogo debes indicar un nombre de producto.")
                    existing = (
                        Product.query.filter_by(user_id=user.id, name=product_name_input).first()
                    )
                    if existing:
                        existing.cost = cost
                        existing.price = price_result
                    else:
                        new_product = Product(
                            user_id=user.id,
                            name=product_name_input,
                            cost=cost,
                            price=price_result,
                        )
                        db.session.add(new_product)
                    db.session.commit()

                result = {
                    "mode": mode,
                    "cost": cost,
                    "margin": margin_used,
                    "price": price_result,
                    "quantity": quantity,
                    "profit_unit": profit_unit,
                    "profit_total": profit_total,
                }

            else:
                if price <= 0:
                    raise ValueError("El precio debe ser mayor que cero.")
                if margin < MIN_MARGIN_PERCENT:
                    raise ValueError(f"El margen debe ser al menos {MIN_MARGIN_PERCENT:.2f} %.")
                if quantity <= 0:
                    raise ValueError("La cantidad debe ser mayor que cero.")

                cost_result = price / (1 + margin / 100.0)
                profit_unit = price - cost_result
                profit_total = profit_unit * quantity
                margin_used = (profit_unit / cost_result * 100.0) if cost_result > 0 else 0.0

                if save_to_catalog:
                    if not product_name_input:
                        raise ValueError("Para guardar en el catálogo debes indicar un nombre de producto.")
                    existing = (
                        Product.query.filter_by(user_id=user.id, name=product_name_input).first()
                    )
                    if existing:
                        existing.cost = cost_result
                        existing.price = price
                    else:
                        new_product = Product(
                            user_id=user.id,
                            name=product_name_input,
                            cost=cost_result,
                            price=price,
                        )
                        db.session.add(new_product)
                    db.session.commit()

                result = {
                    "mode": mode,
                    "cost": cost_result,
                    "margin": margin_used,
                    "price": price,
                    "quantity": quantity,
                    "profit_unit": profit_unit,
                    "profit_total": profit_total,
                }

        except Exception as e:
            error = str(e)

    products = (
        query_for(Product)
        .filter(Product.user_id == user.id)
        .order_by(Product.name.asc())
        .all()
    )
    return render_template(
        "calculadora.html",
        error=error,
        result=result,
        products=products,
        min_margin_percent=MIN_MARGIN_PERCENT,
    )


@app.route("/api/product/<int:product_id>")
@login_required
def api_product(product_id):
    user = current_user()
    product = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    return jsonify(
        {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "cost": product.cost,
            "price": product.price,
        }
    )


# ---------------------------------------------------------
# ERRORES
# ---------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html", error=str(e)), 500


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
