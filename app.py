import os
import io
import datetime as dt

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
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

from openpyxl import Workbook

# -----------------------------------------------------------------------------
# Configuración básica de Flask + SQLAlchemy
# -----------------------------------------------------------------------------

app = Flask(__name__)

# Clave secreta
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

# Base de datos: Render suele exponer DATABASE_URL
db_url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# -----------------------------------------------------------------------------
# Filtro Jinja para formato de números: 1.234,56
# -----------------------------------------------------------------------------
def format_num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0,00"
    s = f"{number:,.2f}"  # 1,234.56
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s


app.jinja_env.filters["format_num"] = format_num


# -----------------------------------------------------------------------------
# Modelos
# -----------------------------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    sales = db.relationship("Sale", backref="user", lazy=True)
    products = db.relationship("Product", backref="user", lazy=True)
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
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # Pagado / Pendiente
    name = db.Column(db.String(120), nullable=False)  # Cliente (texto)
    product = db.Column(db.String(120), nullable=False)
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)

    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date, nullable=True)

    comment = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


# -----------------------------------------------------------------------------
# Utilidades de autenticación y multiusuario
# -----------------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def base_query(model):
    """Si el usuario es admin ve todo, si no solo lo suyo."""
    u = current_user()
    if not u:
        # No debería ocurrir si usamos login_required, pero por seguridad
        return model.query.filter(db.text("1=0"))
    if u.is_admin:
        return model.query
    return model.query.filter_by(user_id=u.id)


def login_required(f):
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


def ensure_admin_user():
    """Crea un usuario admin por defecto si no existe."""
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    db.create_all()
    ensure_admin_user()


# -----------------------------------------------------------------------------
# Rutas de autenticación
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["user"] = user.username
            session["is_admin"] = user.is_admin
            return redirect(url_for("ventas"))
        else:
            error = "Usuario o contraseña incorrectos."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# Gestión de usuarios (solo admin)
# -----------------------------------------------------------------------------
@app.route("/usuarios", methods=["GET", "POST"])
@login_required
def usuarios():
    if not session.get("is_admin"):
        return redirect(url_for("ventas"))

    error = None
    success = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        is_admin_flag = bool(request.form.get("is_admin"))

        if not username or not password:
            error = "Usuario y contraseña son obligatorios."
        else:
            if User.query.filter_by(username=username).first():
                error = "Ya existe un usuario con ese nombre."
            else:
                u = User(
                    username=username,
                    password_hash=generate_password_hash(password),
                    is_admin=is_admin_flag,
                )
                db.session.add(u)
                db.session.commit()
                success = "Usuario creado correctamente."

    users = User.query.order_by(User.username).all()
    return render_template("usuarios.html", error=error, success=success, users=users)


@app.route("/delete_user/<int:user_id>", methods=["POST"])
@login_required
def delete_user(user_id):
    if not session.get("is_admin"):
        return redirect(url_for("ventas"))

    if user_id == session.get("user_id"):
        return redirect(url_for("usuarios"))

    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("usuarios"))


# -----------------------------------------------------------------------------
# Ruta raíz
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("ventas"))
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# Productos (catálogo)
# -----------------------------------------------------------------------------
@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    error = None
    success = None
    u = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        cost_raw = request.form.get("cost", "0").strip()
        price_raw = request.form.get("price", "0").strip()

        try:
            cost = float(cost_raw or 0)
            price = float(price_raw or 0)
        except ValueError:
            cost = 0.0
            price = 0.0

        if not name:
            error = "El nombre del producto es obligatorio."
        else:
            try:
                product = (
                    Product.query.filter_by(name=name, user_id=u.id).first()
                    if u
                    else None
                )
                if not product:
                    product = Product(name=name, user_id=u.id)
                    db.session.add(product)

                product.cost = cost
                product.price = price
                if cost > 0:
                    product.margin_percent = (price - cost) / cost * 100
                else:
                    product.margin_percent = 0.0

                db.session.commit()
                success = "Producto guardado/actualizado correctamente."
            except Exception as e:
                db.session.rollback()
                error = f"Error al guardar el producto: {e}"

    products = base_query(Product).order_by(Product.name).all()

    return render_template(
        "productos.html",
        error=error,
        success=success,
        products=products,
    )


@app.route("/delete_product/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    product = base_query(Product).filter_by(id=product_id).first_or_404()
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos"))


# Exportar productos a Excel
@app.route("/productos_export")
@login_required
def productos_export():
    products = base_query(Product).order_by(Product.name).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Productos"

    headers = ["Nombre", "Costo", "Precio", "Margen (%)"]
    ws.append(headers)

    for p in products:
        ws.append(
            [
                p.name,
                p.cost or 0,
                p.price or 0,
                p.margin_percent or 0,
            ]
        )

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"productos_{dt.date.today().isoformat()}.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# Clientes
# -----------------------------------------------------------------------------
@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    error = None
    success = None
    u = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            error = "El nombre del cliente es obligatorio."
        else:
            try:
                existing = Client.query.filter_by(name=name, user_id=u.id).first()
                if existing:
                    error = (
                        "Ya existe un cliente con ese nombre para tu usuario. "
                        "Edita el existente o usa otro nombre."
                    )
                else:
                    c = Client(
                        name=name,
                        phone=phone,
                        email=email,
                        notes=notes,
                        user_id=u.id,
                    )
                    db.session.add(c)
                    db.session.commit()
                    success = "Cliente registrado correctamente."
            except Exception as e:
                db.session.rollback()
                error = f"Error al guardar el cliente: {e}"

    clients = base_query(Client).order_by(Client.name).all()

    return render_template(
        "clientes.html",
        error=error,
        success=success,
        clients=clients,
    )


@app.route("/delete_client/<int:client_id>", methods=["POST"])
@login_required
def delete_client(client_id):
    client = base_query(Client).filter_by(id=client_id).first_or_404()
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


@app.route("/clientes_export")
@login_required
def clientes_export():
    clients = base_query(Client).order_by(Client.name).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Clientes"

    headers = ["Nombre", "Teléfono", "Email", "Notas"]
    ws.append(headers)

    for c in clients:
        ws.append(
            [
                c.name,
                c.phone or "",
                c.email or "",
                c.notes or "",
            ]
        )

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"clientes_{dt.date.today().isoformat()}.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# Ventas
# -----------------------------------------------------------------------------
@app.route("/ventas", methods=["GET", "POST"])
@login_required
def ventas():
    error = None
    success = None

    # --- alta de venta (POST) ---
    if request.method == "POST":
        try:
            date_str = request.form.get("date") or ""
            status = request.form.get("status") or "Pagado"
            name = request.form.get("name") or ""
            product_select = request.form.get("product_select") or ""
            product_text = request.form.get("product") or ""
            product_name = product_select or product_text

            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)
            quantity = int(request.form.get("quantity") or 1)

            pending_amount = float(request.form.get("pending_amount") or 0)
            due_date_str = request.form.get("due_date") or ""
            comment = request.form.get("comment") or ""

            date = dt.date.fromisoformat(date_str) if date_str else dt.date.today()
            due_date = dt.date.fromisoformat(due_date_str) if due_date_str else None

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            sale = Sale(
                date=date,
                status=status,
                name=name,
                product=product_name,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                quantity=quantity,
                total=total,
                profit=profit,
                pending_amount=pending_amount if status == "Pendiente" else 0,
                due_date=due_date if status == "Pendiente" else None,
                user_id=session["user_id"],
                comment=comment,
            )
            db.session.add(sale)
            db.session.commit()
            success = "Venta registrada correctamente."
        except Exception as e:
            db.session.rollback()
            error = f"Error al registrar la venta: {e}"

    # --- filtros (GET) ---
    filter_name = request.args.get("filter_name", "").strip()
    filter_status = request.args.get("filter_status", "").strip()
    filter_user_id = request.args.get("filter_user_id", "").strip()
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()

    query = base_query(Sale)

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))
    if filter_status:
        query = query.filter(Sale.status == filter_status)
    if filter_user_id:
        try:
            uid = int(filter_user_id)
            query = query.filter(Sale.user_id == uid)
        except ValueError:
            pass
    if date_from_str:
        try:
            df = dt.date.fromisoformat(date_from_str)
            query = query.filter(Sale.date >= df)
        except ValueError:
            pass
    if date_to_str:
        try:
            dt_to = dt.date.fromisoformat(date_to_str)
            query = query.filter(Sale.date <= dt_to)
        except ValueError:
            pass

    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    total_ventas = len(sales)
    total_monto = sum(s.total or 0 for s in sales)
    total_ganancia = sum(s.profit or 0 for s in sales)
    total_pendiente = sum((s.pending_amount or 0) for s in sales if s.status == "Pendiente")
    total_pagado = total_monto - total_pendiente

    users = User.query.order_by(User.username).all()

    products = base_query(Product).order_by(Product.name).all()
    product_mapping = {
        p.name: {
            "cost": float(p.cost or 0),
            "price": float(p.price or 0),
        }
        for p in products
    }

    clients = base_query(Client).order_by(Client.name).all()

    return render_template(
        "ventas.html",
        error=error,
        success=success,
        sales=sales,
        total_ventas=total_ventas,
        total_monto=total_monto,
        total_ganancia=total_ganancia,
        total_pendiente=total_pendiente,
        total_pagado=total_pagado,
        filter_name=filter_name,
        filter_status=filter_status,
        filter_user_id=filter_user_id,
        date_from=date_from_str,
        date_to=date_to_str,
        users=users,
        products=products,
        product_mapping=product_mapping,
        clients=clients,
    )


@app.route("/delete_sale/<int:sale_id>", methods=["POST"])
@login_required
def delete_sale(sale_id):
    sale = base_query(Sale).filter_by(id=sale_id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas"))


@app.route("/mark_sale_paid/<int:sale_id>", methods=["POST"])
@login_required
def mark_sale_paid(sale_id):
    sale = base_query(Sale).filter_by(id=sale_id).first_or_404()
    sale.status = "Pagado"
    sale.pending_amount = 0.0
    sale.due_date = None
    db.session.commit()
    return redirect(url_for("ventas"))


@app.route("/ventas_export")
@login_required
def ventas_export():
    filter_name = request.args.get("filter_name", "").strip()
    filter_status = request.args.get("filter_status", "").strip()
    filter_user_id = request.args.get("filter_user_id", "").strip()
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()

    query = base_query(Sale)

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))
    if filter_status:
        query = query.filter(Sale.status == filter_status)
    if filter_user_id:
        try:
            uid = int(filter_user_id)
            query = query.filter(Sale.user_id == uid)
        except ValueError:
            pass
    if date_from_str:
        try:
            df = dt.date.fromisoformat(date_from_str)
            query = query.filter(Sale.date >= df)
        except ValueError:
            pass
    if date_to_str:
        try:
            dt_to = dt.date.fromisoformat(date_to_str)
            query = query.filter(Sale.date <= dt_to)
        except ValueError:
            pass

    sales = query.order_by(Sale.date, Sale.id).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Ventas"

    headers = [
        "Fecha",
        "Cliente",
        "Producto",
        "Estado",
        "Costo U.",
        "Precio U.",
        "Cantidad",
        "Total",
        "Ganancia",
        "Pendiente",
        "F. compromiso",
        "Usuario",
        "Comentario",
    ]
    ws.append(headers)

    for s in sales:
        ws.append(
            [
                s.date.isoformat() if s.date else "",
                s.name,
                s.product,
                s.status,
                s.cost_per_unit or 0,
                s.price_per_unit or 0,
                s.quantity or 0,
                s.total or 0,
                s.profit or 0,
                s.pending_amount or 0,
                s.due_date.isoformat() if s.due_date else "",
                s.user.username if s.user else "",
                s.comment or "",
            ]
        )

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"ventas_{dt.date.today().isoformat()}.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# Flujo de caja (gastos / reinversión)
# -----------------------------------------------------------------------------
@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
    error = None
    success = None

    if request.method == "POST":
        try:
            date_str = request.form.get("date") or ""
            category = request.form.get("category") or "Gasto"
            description = request.form.get("description", "").strip()
            amount = float(request.form.get("amount") or 0)

            date = dt.date.fromisoformat(date_str) if date_str else dt.date.today()

            if not description:
                error = "La descripción es obligatoria."
            else:
                e = Expense(
                    date=date,
                    category=category,
                    description=description,
                    amount=amount,
                    user_id=session["user_id"],
                )
                db.session.add(e)
                db.session.commit()
                success = "Movimiento registrado correctamente."
        except Exception as ex:
            db.session.rollback()
            error = f"Error al registrar el movimiento: {ex}"

    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    category_filter = request.args.get("category_filter", "").strip()

    q_sales = base_query(Sale)
    q_exp = base_query(Expense)

    if date_from_str:
        try:
            df = dt.date.fromisoformat(date_from_str)
            q_sales = q_sales.filter(Sale.date >= df)
            q_exp = q_exp.filter(Expense.date >= df)
        except ValueError:
            pass
    if date_to_str:
        try:
            dt_to = dt.date.fromisoformat(date_to_str)
            q_sales = q_sales.filter(Sale.date <= dt_to)
            q_exp = q_exp.filter(Expense.date <= dt_to)
        except ValueError:
            pass

    if category_filter:
        q_exp = q_exp.filter(Expense.category == category_filter)

    expenses = q_exp.order_by(Expense.date.desc(), Expense.id.desc()).all()

    total_ingresos = sum(s.total or 0 for s in q_sales.all())
    total_ganancia = sum(s.profit or 0 for s in q_sales.all())
    total_gastos = sum(e.amount or 0 for e in expenses if e.category == "Gasto")
    total_reinv = sum(e.amount or 0 for e in expenses if e.category == "Reinversión")
    total_egresos = total_gastos + total_reinv
    neto = total_ganancia - total_egresos

    # Objetivo de ahorro 10% de la ganancia
    ahorro_objetivo = total_ganancia * 0.10
    ahorro_real = max(total_ganancia - total_egresos, 0.0)
    ahorro_faltante = max(ahorro_objetivo - ahorro_real, 0.0)
    meta_cumplida = ahorro_real >= ahorro_objetivo

    return render_template(
        "flujo.html",
        error=error,
        success=success,
        expenses=expenses,
        date_from=date_from_str,
        date_to=date_to_str,
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


@app.route("/delete_expense/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    exp = base_query(Expense).filter_by(id=expense_id).first_or_404()
    db.session.delete(exp)
    db.session.commit()
    return redirect(url_for("flujo"))


@app.route("/flujo_export")
@login_required
def flujo_export():
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    category_filter = request.args.get("category_filter", "").strip()

    q_exp = base_query(Expense)

    if date_from_str:
        try:
            df = dt.date.fromisoformat(date_from_str)
            q_exp = q_exp.filter(Expense.date >= df)
        except ValueError:
            pass
    if date_to_str:
        try:
            dt_to = dt.date.fromisoformat(date_to_str)
            q_exp = q_exp.filter(Expense.date <= dt_to)
        except ValueError:
            pass
    if category_filter:
        q_exp = q_exp.filter(Expense.category == category_filter)

    expenses = q_exp.order_by(Expense.date, Expense.id).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Flujo"

    headers = ["Fecha", "Descripción", "Tipo", "Monto"]
    ws.append(headers)

    for e in expenses:
        ws.append(
            [
                e.date.isoformat() if e.date else "",
                e.description,
                e.category,
                e.amount or 0,
            ]
        )

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"flujo_{dt.date.today().isoformat()}.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# Calculadora de precios (separada)
# -----------------------------------------------------------------------------
@app.route("/calculadora", methods=["GET", "POST"])
@login_required
def calculadora():
    error = None
    success = None

    min_margin = 0.0  # margen mínimo desactivado, el usuario define

    product_name_input = ""
    cost_input = ""
    margin_input = ""
    quantity_input = "1"

    price_result = None
    profit_unit = 0.0
    profit_total = 0.0
    margin_used = 0.0

    products = base_query(Product).order_by(Product.name).all()

    if request.method == "POST":
        product_name_input = request.form.get("product_name", "").strip()
        cost_raw = request.form.get("cost", "").strip()
        margin_raw = request.form.get("margin", "").strip()
        quantity_raw = request.form.get("quantity", "1").strip()
        save_to_catalog = bool(request.form.get("save_to_catalog"))

        try:
            cost = float(cost_raw or 0)
            margin = float(margin_raw or 0)
            quantity = int(quantity_raw or 1)
        except ValueError:
            error = "Revisa que costo, margen y cantidad sean numéricos."
            quantity = 1
            cost = 0.0
            margin = 0.0
        else:
            if cost <= 0:
                error = "El costo debe ser mayor a 0."
            else:
                margin_used = margin
                price_result = cost * (1 + margin_used / 100.0)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity

                if save_to_catalog:
                    if not product_name_input:
                        error = "Debes indicar un nombre de producto para guardar en el catálogo."
                    else:
                        try:
                            u = current_user()
                            product = (
                                Product.query.filter_by(
                                    name=product_name_input, user_id=u.id
                                ).first()
                                if u
                                else None
                            )
                            if not product:
                                product = Product(name=product_name_input, user_id=u.id)
                                db.session.add(product)
                            product.cost = cost
                            product.price = price_result
                            product.margin_percent = margin_used
                            db.session.commit()
                            success = "Producto guardado/actualizado en el catálogo."
                            products = base_query(Product).order_by(Product.name).all()
                        except Exception as ex:
                            db.session.rollback()
                            error = f"Error al guardar en catálogo: {ex}"

        cost_input = cost_raw
        margin_input = margin_raw
        quantity_input = quantity_raw

    return render_template(
        "calculadora.html",
        error=error,
        success=success,
        min_margin=min_margin,
        product_name_input=product_name_input,
        cost_input=cost_input,
        margin_input=margin_input,
        quantity_input=quantity_input,
        price_result=price_result,
        profit_unit=profit_unit,
        profit_total=profit_total,
        margin_used=margin_used,
        products=products,
    )


# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    # Rango de fechas
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    preset = request.args.get("preset", "").strip()

    today = dt.date.today()

    if preset == "week":
        date_to = today
        date_from = today - dt.timedelta(days=6)
    elif preset == "4weeks":
        date_to = today
        date_from = today - dt.timedelta(weeks=4)
    elif preset == "month":
        date_to = today
        date_from = today.replace(day=1)
    elif preset == "year":
        date_to = today
        date_from = today.replace(month=1, day=1)
    else:
        date_from = None
        date_to = None
        if date_from_str:
            try:
                date_from = dt.date.fromisoformat(date_from_str)
            except ValueError:
                date_from = None
        if date_to_str:
            try:
                date_to = dt.date.fromisoformat(date_to_str)
            except ValueError:
                date_to = None

    if not date_from:
        date_from = today.replace(day=1)
    if not date_to:
        date_to = today

    date_from_str = date_from.isoformat()
    date_to_str = date_to.isoformat()

    sales_q = base_query(Sale).filter(Sale.date >= date_from, Sale.date <= date_to)
    sales = sales_q.all()

    total_ganancia = sum(s.profit or 0 for s in sales)
    total_monto_period = sum(s.total or 0 for s in sales)
    total_ventas_period = len(sales)

    avg_ticket = total_monto_period / total_ventas_period if total_ventas_period > 0 else 0.0
    days_span = (date_to - date_from).days + 1
    avg_daily_profit = total_ganancia / days_span if days_span > 0 else 0.0

    # Top productos por ganancia
    top_data = {}
    for s in sales:
        if not s.product:
            continue
        top_data.setdefault(s.product, 0.0)
        top_data[s.product] += s.profit or 0
    sorted_top = sorted(top_data.items(), key=lambda x: x[1], reverse=True)[:5]
    top_labels = [t[0] for t in sorted_top]
    top_values = [round(t[1], 2) for t in sorted_top]

    # Ganancia por semana (ISO week)
    weekly = {}
    for s in sales:
        if not s.date:
            continue
        year, week, _ = s.date.isocalendar()
        key = f"{year}-W{week:02d}"
        weekly.setdefault(key, 0.0)
        weekly[key] += s.profit or 0
    week_labels = sorted(weekly.keys())
    week_values = [round(weekly[k], 2) for k in week_labels]

    # Ganancia por usuario
    sales_all_range = sales_q.all()
    user_profit = {}
    for s in sales_all_range:
        uname = s.user.username if s.user else "N/A"
        user_profit.setdefault(uname, 0.0)
        user_profit[uname] += s.profit or 0
    user_labels = list(user_profit.keys())
    user_values = [round(user_profit[k], 2) for k in user_labels]

    # Alertas inteligentes + métricas de morosidad
    alerts = []

    # Pendientes vencidos
    today = dt.date.today()
    overdue_q = base_query(Sale).filter(
        Sale.status == "Pendiente",
        Sale.due_date.isnot(None),
        Sale.due_date < today,
    )
    overdue_sales = overdue_q.all()
    overdue_total = sum(s.pending_amount or 0 for s in overdue_sales)
    overdue_count = len(overdue_sales)

    if overdue_count > 0:
        alerts.append(
            {
                "level": "danger",
                "title": "Pagos vencidos",
                "message": f"Tienes {overdue_count} ventas pendientes vencidas por un total de ₡{format_num(overdue_total)}.",
            }
        )

    # Pendientes próximos 7 días
    upcoming_q = base_query(Sale).filter(
        Sale.status == "Pendiente",
        Sale.due_date.isnot(None),
        Sale.due_date >= today,
        Sale.due_date <= today + dt.timedelta(days=7),
    )
    upcoming_sales = upcoming_q.all()
    upcoming_total = sum(s.pending_amount or 0 for s in upcoming_sales)
    upcoming_count = len(upcoming_sales)

    if upcoming_count > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Pagos próximos",
                "message": f"En los próximos 7 días vencen {upcoming_count} pagos por un total de ₡{format_num(upcoming_total)}.",
            }
        )

    # Alerta de baja ganancia
    if avg_daily_profit < 10000 and total_ganancia > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Ganancia diaria baja",
                "message": "Tu ganancia diaria promedio es relativamente baja. Revisa precios, costos y volumen de ventas.",
            }
        )

    # Alerta si no hay ventas
    if total_ventas_period == 0:
        alerts.append(
            {
                "level": "info",
                "title": "Sin ventas en el rango",
                "message": "No se registran ventas en el rango de fechas seleccionado.",
            }
        )

    # Ganancia mínima por semana (para mostrar en alguna parte si quieres)
    min_weekly_profit = min(week_values) if week_values else 0.0

    return render_template(
        "dashboard.html",
        date_from=date_from_str,
        date_to=date_to_str,
        total_ganancia=total_ganancia,
        total_monto_period=total_monto_period,
        avg_ticket=avg_ticket,
        total_ventas_period=total_ventas_period,
        avg_daily_profit=avg_daily_profit,
        top_labels=top_labels,
        top_values=top_values,
        week_labels=week_labels,
        week_values=week_values,
        user_labels=user_labels,
        user_values=user_values,
        alerts=alerts,
        overdue_total=overdue_total,
        upcoming_total=upcoming_total,
        overdue_count=overdue_count,
        upcoming_count=upcoming_count,
        min_weekly_profit=min_weekly_profit,
    )


# -----------------------------------------------------------------------------
# Main (desarrollo local)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
