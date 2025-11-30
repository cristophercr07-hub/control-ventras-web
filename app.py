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
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# ---------------------------------------------------------

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "cambia-esta-clave-a-una-muy-segura"
)

# Soporte Render: DATABASE_URL (Postgres) o SQLite local
database_url = os.environ.get("DATABASE_URL", "sqlite:///ventas.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Margen mínimo de utilidad para la calculadora (7 %)
MIN_MARGIN_PERCENT = 7.0


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    products = db.relationship("Product", backref="user", lazy=True)
    sales = db.relationship("Sale", backref="user", lazy=True)
    expenses = db.relationship("Expense", backref="user", lazy=True)
    clients = db.relationship("Client", backref="user", lazy=True)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notes = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # Pagado / Pendiente
    name = db.Column(db.String(120), nullable=False)   # Cliente (texto)
    product = db.Column(db.String(120), nullable=False)
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)
    amount_paid = db.Column(db.Float, default=0.0)
    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date)
    notes = db.Column(db.String(255))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(20), nullable=False)  # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


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


def current_user_id():
    return session.get("user_id")


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user_id() or not session.get("is_admin"):
            return redirect(url_for("ventas"))
        return f(*args, **kwargs)
    return wrapper


def format_num(value):
    try:
        n = float(value or 0)
    except Exception:
        return "0"
    # Separador de miles tipo 12,345.67
    return f"{n:,.2f}"


app.jinja_env.filters["format_num"] = format_num


# ---------------------------------------------------------
# INICIALIZACIÓN BD + USUARIO ADMIN
# ---------------------------------------------------------

with app.app_context():
    db.create_all()
    # Usuario admin por defecto
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
    if current_user_id():
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
@admin_required
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


# ---------------------------------------------------------
# RUTA RAÍZ
# ---------------------------------------------------------

@app.route("/")
def index():
    if not current_user_id():
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# PRODUCTOS + CALCULADORA
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    error = None
    success = None

    # Valores por defecto calculadora
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

        # --- CALCULADORA ---
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
                        raise ValueError("Para guardar debes indicar un nombre de producto.")
                    existing = Product.query.filter_by(
                        user_id=current_user_id(),
                        name=product_name_input,
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
                            user_id=current_user_id(),
                        )
                        db.session.add(p)
                    db.session.commit()
                    success = "Producto guardado/actualizado en el catálogo."
            except Exception as e:
                error = f"Error en la calculadora: {e}"

        # --- FORMULARIO DIRECTO CATÁLOGO ---
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

                existing = Product.query.filter_by(
                    user_id=current_user_id(),
                    name=name,
                ).first()
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
                        user_id=current_user_id(),
                    )
                    db.session.add(p)
                    db.session.commit()
                    success = "Producto creado correctamente."
            except Exception as e:
                error = f"Error al guardar el producto: {e}"

    products = Product.query.filter_by(user_id=current_user_id()).order_by(Product.name).all()
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
    product = Product.query.filter_by(
        id=product_id,
        user_id=current_user_id(),
    ).first_or_404()
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos"))


@app.route("/calculadora", methods=["GET", "POST"])
@login_required
def calculadora():
    # Redirigimos al combo Productos+Calculadora
    return redirect(url_for("productos"))


# ---------------------------------------------------------
# CLIENTES
# ---------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    error = None
    success = None

    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            notes = (request.form.get("notes") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")

            existing = Client.query.filter_by(
                user_id=current_user_id(),
                name=name,
            ).first()

            if existing:
                existing.phone = phone
                existing.email = email
                existing.notes = notes
                db.session.commit()
                success = "Cliente actualizado correctamente."
            else:
                c = Client(
                    name=name,
                    phone=phone,
                    email=email,
                    notes=notes,
                    user_id=current_user_id(),
                )
                db.session.add(c)
                db.session.commit()
                success = "Cliente creado correctamente."

        except Exception as e:
            error = f"Error al guardar el cliente: {e}"

    clients = (
        Client.query.filter_by(user_id=current_user_id())
        .order_by(Client.name)
        .all()
    )

    return render_template(
        "clientes.html",
        error=error,
        success=success,
        clients=clients,
    )


@app.post("/clientes/<int:client_id>/delete")
@login_required
def delete_client(client_id):
    client = Client.query.filter_by(
        id=client_id,
        user_id=current_user_id(),
    ).first_or_404()

    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


# ---------------------------------------------------------
# VENTAS
# ---------------------------------------------------------

def apply_sales_filters(query, filter_name, filter_status, date_from_str, date_to_str):
    if filter_name:
        like_pattern = f"%{filter_name}%"
        query = query.filter(Sale.name.ilike(like_pattern))

    if filter_status:
        query = query.filter(Sale.status == filter_status)

    d_from = parse_date(date_from_str)
    d_to = parse_date(date_to_str)
    if d_from:
        query = query.filter(Sale.date >= d_from)
    if d_to:
        query = query.filter(Sale.date <= d_to)

    return query


@app.route("/ventas", methods=["GET", "POST"])
@login_required
def ventas():
    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date = parse_date(date_str) or datetime.date.today()

            name = (request.form.get("name") or "").strip()

            # Cliente opcional vinculado
            client_from_select = (request.form.get("client_select") or "").strip()
            if not name and client_from_select:
                name = client_from_select

            product_from_select = (request.form.get("product_select") or "").strip()
            product_input = (request.form.get("product") or "").strip()
            product = product_input or product_from_select

            status = request.form.get("status") or "Pagado"
            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)
            quantity = int(request.form.get("quantity") or 1)
            amount_paid = float(request.form.get("amount_paid") or 0)
            notes = (request.form.get("notes") or "").strip()
            due_date_str = request.form.get("due_date")
            due_date = parse_date(due_date_str)

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")
            if not product:
                raise ValueError("Debes seleccionar o escribir un producto.")
            if quantity <= 0:
                raise ValueError("La cantidad debe ser mayor que cero.")

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            if status == "Pagado":
                amount_paid = total
                pending_amount = 0.0
                due_date = None
            else:
                pending_amount = total - amount_paid
                if pending_amount < 0:
                    pending_amount = 0.0

            sale = Sale(
                date=date,
                status=status,
                name=name,
                product=product,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                quantity=quantity,
                total=total,
                profit=profit,
                amount_paid=amount_paid,
                pending_amount=pending_amount,
                due_date=due_date,
                notes=notes,
                user_id=current_user_id(),
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

    query = Sale.query.filter_by(user_id=current_user_id())
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    total_monto = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    total_pagado = sum(float(s.amount_paid or 0) for s in sales)
    total_pendiente = sum(float(s.pending_amount or 0) for s in sales)

    products = Product.query.filter_by(user_id=current_user_id()).order_by(Product.name).all()
    clients = Client.query.filter_by(user_id=current_user_id()).order_by(Client.name).all()

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
        total_monto=total_monto,
        total_ganancia=total_ganancia,
        total_pagado=total_pagado,
        total_pendiente=total_pendiente,
    )


@app.post("/ventas/<int:sale_id>/delete")
@login_required
def delete_sale(sale_id):
    sale = Sale.query.filter_by(
        id=sale_id,
        user_id=current_user_id(),
    ).first_or_404()
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

    query = Sale.query.filter_by(user_id=current_user_id())
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)
    sales = query.order_by(Sale.date.asc(), Sale.id.asc()).all()

    rows = []
    for s in sales:
        rows.append(
            {
                "Fecha": s.date.isoformat() if s.date else "",
                "Cliente": s.name,
                "Producto": s.product,
                "Estado": s.status,
                "Costo por unidad": s.cost_per_unit,
                "Precio por unidad": s.price_per_unit,
                "Cantidad": s.quantity,
                "Total": s.total,
                "Ganancia": s.profit,
                "Monto pagado": s.amount_paid,
                "Monto pendiente": s.pending_amount,
                "Fecha vencimiento": s.due_date.isoformat() if s.due_date else "",
                "Notas": s.notes or "",
            }
        )

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
@login_required
def flujo():
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
                user_id=current_user_id(),
            )
            db.session.add(e)
            db.session.commit()
            success = "Movimiento registrado correctamente."
        except Exception as e:
            error = f"Error al registrar movimiento: {e}"

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = Expense.query.filter_by(user_id=current_user_id())
    sales_query = Sale.query.filter_by(user_id=current_user_id())

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

    exp_query = Expense.query.filter_by(user_id=current_user_id())

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
        rows.append(
            {
                "Fecha": e.date.isoformat() if e.date else "",
                "Descripción": e.description,
                "Tipo": e.category,
                "Monto": e.amount,
            }
        )

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

    sales_query = Sale.query.filter_by(user_id=current_user_id())

    d_from = None
    d_to = None

    if preset:
        today = datetime.date.today()

        if preset == "week":            # últimos 7 días
            d_to = today
            d_from = today - datetime.timedelta(days=7)
        elif preset == "4weeks":        # últimas 4 semanas
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

    # Totales básicos
    total_monto_period = sum(float(s.total or 0) for s in sales)
    total_ganancia = sum(float(s.profit or 0) for s in sales)
    num_ventas = len(sales)
    avg_ticket = total_monto_period / num_ventas if num_ventas > 0 else 0.0

    # Ganancia diaria promedio
    if d_from and d_to:
        days_range = (d_to - d_from).days + 1
    elif sales:
        first_date = min(s.date for s in sales if s.date)
        last_date = max(s.date for s in sales if s.date)
        days_range = (last_date - first_date).days + 1
    else:
        days_range = 0

    avg_daily_profit = total_ganancia / days_range if days_range > 0 else 0.0

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

    max_weekly_profit = max(week_values) if week_values else 0.0
    min_weekly_profit = min(week_values) if week_values else 0.0

    # Alertas por pagos vencidos / próximos
    today = datetime.date.today()
    soon_limit = today + datetime.timedelta(days=7)

    overdue_total = 0.0
    overdue_count = 0
    upcoming_total = 0.0
    upcoming_count = 0

    for s in sales:
        if s.status == "Pendiente" and s.pending_amount and s.due_date:
            if s.due_date < today:
                overdue_total += float(s.pending_amount or 0)
                overdue_count += 1
            elif today <= s.due_date <= soon_limit:
                upcoming_total += float(s.pending_amount or 0)
                upcoming_count += 1

    alerts = []
    if overdue_total > 0:
        alerts.append(
            {
                "level": "danger",
                "title": "Pagos vencidos",
                "message": f"Tienes {overdue_count} venta(s) con pagos vencidos por cobrar.",
            }
        )
    if upcoming_total > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Pagos próximos",
                "message": f"En los próximos días vencen pagos de {upcoming_count} venta(s).",
            }
        )

    return render_template(
        "dashboard.html",
        alerts=alerts,
        week_labels=week_labels,
        week_values=week_values,
        top_labels=top_labels,
        top_values=top_values,
        total_ganancia=total_ganancia,
        total_monto_period=total_monto_period,
        avg_ticket=avg_ticket,
        avg_daily_profit=avg_daily_profit,
        max_weekly_profit=max_weekly_profit,
        min_weekly_profit=min_weekly_profit,
        overdue_total=overdue_total,
        overdue_count=overdue_count,
        upcoming_total=upcoming_total,
        upcoming_count=upcoming_count,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------
# MAIN LOCAL
# ---------------------------------------------------------

if __name__ == "__main__":
    # Para desarrollo local; en Render usas gunicorn
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
