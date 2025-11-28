import os
import io
from datetime import datetime, date, timedelta

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
from sqlalchemy import func, UniqueConstraint
from openpyxl import Workbook

# -----------------------------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# -----------------------------------------------------------------------------
app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "super-secret-key-change-me")

# Base de datos: usa DATABASE_URL (Render) o SQLite local como fallback
database_url = os.getenv("DATABASE_URL", "sqlite:///ventas.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# -----------------------------------------------------------------------------
# FILTRO JINJA PARA FORMATO NUMÉRICO
#   Ej: 1234567.89 -> "1.234.567,89"
# -----------------------------------------------------------------------------
def format_num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value

    integer_part, dot, frac = f"{number:,.2f}".partition(".")
    # "," de miles lo convertimos a "." y "." decimal a ","
    integer_part = integer_part.replace(",", ".")
    return integer_part + ("," + frac if frac else "")


app.jinja_env.filters["format_num"] = format_num


# -----------------------------------------------------------------------------
# DECORADOR LOGIN REQUIRED
# -----------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


# -----------------------------------------------------------------------------
# MODELOS
# -----------------------------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    sales = db.relationship("Sale", backref="user", lazy=True)
    expenses = db.relationship("Expense", backref="user", lazy=True)
    products = db.relationship("Product", backref="user", lazy=True)
    clients = db.relationship("Client", backref="user", lazy=True)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Client(db.Model):
    __tablename__ = "client"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    notes = db.Column(db.String(255))  # Asegúrate de que la columna exista en la BD
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Unicidad por usuario + nombre (lógico). La BD puede seguir teniendo
    # la constraint antigua global; a nivel de app hacemos upsert.
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uix_user_client_name"),
    )


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(50), default="Pagado")  # Pagado / Pendiente
    name = db.Column(db.String(255), nullable=True)  # Nombre del cliente (texto)
    product = db.Column(db.String(255), nullable=False)
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)
    comment = db.Column(db.String(255))

    # Relación con usuario (multiusuario)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Fecha de pago comprometida (para pendientes)
    due_date = db.Column(db.Date, nullable=True)

    # Relación opcional con Client (para poder seleccionar cliente guardado)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)
    client = db.relationship("Client", backref="sales", lazy=True)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=True)
    description = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(50), nullable=False)  # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


# -----------------------------------------------------------------------------
# INICIALIZACIÓN BD + USUARIO ADMIN
# -----------------------------------------------------------------------------
def init_db():
    db.create_all()

    # Crear admin por defecto si no existe
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    init_db()


# -----------------------------------------------------------------------------
# RUTAS DE AUTENTICACIÓN
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            error = "Usuario o contraseña incorrectos."
            return render_template("login.html", error=error)

        session["user_id"] = user.id
        session["user"] = user.username
        session["is_admin"] = bool(user.is_admin)

        return redirect(url_for("dashboard"))

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# GESTIÓN DE USUARIOS (SOLO ADMIN)
# -----------------------------------------------------------------------------
@app.route("/usuarios", methods=["GET", "POST"])
@login_required
def usuarios():
    if not session.get("is_admin"):
        return redirect(url_for("dashboard"))

    error = None
    success = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
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

    users = User.query.order_by(User.username.asc()).all()

    return render_template(
        "usuarios.html",
        error=error,
        success=success,
        users=users,
    )


@app.route("/delete_user/<int:user_id>", methods=["POST"])
@login_required
def delete_user(user_id):
    if not session.get("is_admin"):
        return redirect(url_for("dashboard"))

    current_user_id = session.get("user_id")
    if user_id == current_user_id:
        # No permitir borrar el propio usuario
        return redirect(url_for("usuarios"))

    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("usuarios"))


# -----------------------------------------------------------------------------
# CLIENTES
# -----------------------------------------------------------------------------
@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    error = None
    success = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            error = "El nombre del cliente es obligatorio."
        else:
            try:
                # "Upsert" por usuario + nombre
                existing = Client.query.filter_by(user_id=user_id, name=name).first()
                if existing:
                    existing.phone = phone
                    existing.email = email
                    existing.notes = notes
                    success = "Cliente actualizado correctamente."
                else:
                    new_client = Client(
                        name=name,
                        phone=phone,
                        email=email,
                        notes=notes,
                        user_id=user_id,
                    )
                    db.session.add(new_client)
                    success = "Cliente creado correctamente."

                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                error = f"Error al guardar el cliente: {exc}"

    query = Client.query
    if not is_admin:
        query = query.filter(Client.user_id == user_id)

    clients = query.order_by(Client.name.asc()).all()

    return render_template(
        "clientes.html",
        error=error,
        success=success,
        clients=clients,
    )


@app.route("/delete_client/<int:client_id>", methods=["POST"])
@login_required
def delete_client(client_id):
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    client = Client.query.get_or_404(client_id)

    if not is_admin and client.user_id != user_id:
        return redirect(url_for("clientes"))

    try:
        # Quitar referencia en ventas para evitar problemas de FK
        for s in client.sales:
            s.client_id = None
        db.session.delete(client)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(url_for("clientes"))


@app.route("/clientes_export")
@login_required
def clientes_export():
    """Exportar clientes a Excel (XLSX). Respeta el multiusuario."""
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    query = Client.query
    if not is_admin:
        query = query.filter(Client.user_id == user_id)

    clients = query.order_by(Client.name.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Clientes"

    headers = [
        "ID",
        "Nombre",
        "Teléfono",
        "Email",
        "Notas",
        "Usuario",
    ]
    ws.append(headers)

    for c in clients:
        username = c.user.username if getattr(c, "user", None) else ""
        ws.append(
            [
                c.id,
                c.name or "",
                c.phone or "",
                c.email or "",
                c.notes or "",
                username,
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"clientes_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# PRODUCTOS + CALCULADORA
# -----------------------------------------------------------------------------
@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    error = None
    success = None

    min_margin = 30.0  # margen mínimo recomendado

    product_name_input = ""
    cost_input = ""
    margin_input = ""
    quantity_input = ""

    price_result = None
    profit_unit = None
    profit_total = None
    margin_used = None

    if request.method == "POST":
        form_type = request.form.get("form_type", "calculator")

        if form_type == "calculator":
            product_name_input = request.form.get("product_name", "").strip()
            cost_str = request.form.get("cost", "0").replace(",", ".")
            margin_str = request.form.get("margin", "0").replace(",", ".")
            quantity_str = request.form.get("quantity", "1")

            try:
                cost = float(cost_str)
                margin_pct = float(margin_str)
                quantity = int(quantity_str) if quantity_str else 1
                if quantity < 1:
                    quantity = 1

                if margin_pct < min_margin:
                    margin_pct = min_margin

                price_result = cost * (1 + margin_pct / 100.0)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity
                margin_used = margin_pct

                save_to_catalog = bool(request.form.get("save_to_catalog"))
                if save_to_catalog and product_name_input:
                    existing = Product.query.filter_by(
                        user_id=user_id, name=product_name_input
                    ).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price_result
                        existing.margin_percent = margin_used
                        success = "Producto actualizado en el catálogo."
                    else:
                        new_product = Product(
                            name=product_name_input,
                            cost=cost,
                            price=price_result,
                            margin_percent=margin_used,
                            user_id=user_id,
                        )
                            # noqa
                        db.session.add(new_product)
                        success = "Producto guardado en el catálogo."

                    db.session.commit()

                cost_input = f"{cost:.2f}"
                margin_input = f"{margin_pct:.2f}"
                quantity_input = str(quantity)

            except ValueError:
                error = "Datos inválidos en la calculadora. Revisa costo, margen y cantidad."

        elif form_type == "catalog":
            name = request.form.get("name", "").strip()
            cost_str = request.form.get("cost", "0").replace(",", ".")
            price_str = request.form.get("price", "0").replace(",", ".")

            if not name:
                error = "El nombre del producto es obligatorio."
            else:
                try:
                    cost = float(cost_str)
                    price = float(price_str)
                    if cost > 0:
                        margin_pct = (price - cost) / cost * 100.0
                    else:
                        margin_pct = 0.0

                    existing = Product.query.filter_by(
                        user_id=user_id, name=name
                    ).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price
                        existing.margin_percent = margin_pct
                        success = "Producto actualizado correctamente."
                    else:
                        new_product = Product(
                            name=name,
                            cost=cost,
                            price=price,
                            margin_percent=margin_pct,
                            user_id=user_id,
                        )
                        db.session.add(new_product)
                        success = "Producto creado correctamente."

                    db.session.commit()
                except ValueError:
                    error = "Valores numéricos inválidos en costo o precio."

    query = Product.query
    if not is_admin:
        query = query.filter(Product.user_id == user_id)

    products = query.order_by(Product.name.asc()).all()

    return render_template(
        "productos.html",
        error=error,
        success=success,
        products=products,
        min_margin=min_margin,
        product_name_input=product_name_input,
        cost_input=cost_input,
        margin_input=margin_input,
        quantity_input=quantity_input,
        price_result=price_result,
        profit_unit=profit_unit,
        profit_total=profit_total,
        margin_used=margin_used,
    )


@app.route("/delete_product/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    product = Product.query.get_or_404(product_id)
    if not is_admin and product.user_id != user_id:
        return redirect(url_for("productos"))

    try:
        db.session.delete(product)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(url_for("productos"))


@app.route("/productos_export")
@login_required
def productos_export():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    query = Product.query
    if not is_admin:
        query = query.filter(Product.user_id == user_id)

    products = query.order_by(Product.name.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Productos"

    headers = [
        "ID",
        "Nombre",
        "Costo",
        "Precio",
        "Margen (%)",
        "Usuario",
    ]
    ws.append(headers)

    for p in products:
        username = p.user.username if getattr(p, "user", None) else ""
        ws.append(
            [
                p.id,
                p.name or "",
                p.cost or 0.0,
                p.price or 0.0,
                p.margin_percent or 0.0,
                username,
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"productos_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# VENTAS
# -----------------------------------------------------------------------------
@app.route("/ventas", methods=["GET", "POST"])
@login_required
def ventas():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    error = None
    success = None

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        status = request.form.get("status", "Pagado")
        client_id_str = request.form.get("client_id", "").strip()
        name = request.form.get("name", "").strip()
        product_name = request.form.get("product", "").strip()
        cost_str = request.form.get("cost_per_unit", "0").replace(",", ".")
        price_str = request.form.get("price_per_unit", "0").replace(",", ".")
        quantity_str = request.form.get("quantity", "1")
        comment = request.form.get("comment", "").strip()
        due_date_str = request.form.get("due_date", "").strip()

        try:
            if date_str:
                sale_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                sale_date = date.today()

            cost = float(cost_str)
            price = float(price_str)
            quantity = int(quantity_str) if quantity_str else 1
            if quantity < 1:
                quantity = 1

            total = price * quantity
            profit = (price - cost) * quantity

            due_date = None
            if status == "Pendiente" and due_date_str:
                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                except ValueError:
                    due_date = None

            sale = Sale(
                date=sale_date,
                status=status,
                name=name,
                product=product_name,
                cost_per_unit=cost,
                price_per_unit=price,
                quantity=quantity,
                total=total,
                profit=profit,
                comment=comment,
                user_id=user_id,
                due_date=due_date,
            )

            if client_id_str:
                try:
                    cid = int(client_id_str)
                    client = Client.query.get(cid)
                    if client and (is_admin or client.user_id == user_id):
                        sale.client_id = client.id
                        # Si no se ingresó nombre manual, usamos el del cliente
                        if not name:
                            sale.name = client.name
                except ValueError:
                    pass

            db.session.add(sale)
            db.session.commit()
            success = "Venta registrada correctamente."

        except ValueError:
            error = "Datos inválidos en la venta. Revisa costo, precio y cantidad."
        except Exception as exc:
            db.session.rollback()
            error = f"Error al guardar la venta: {exc}"

    # Filtros GET
    filter_name = request.args.get("filter_name", "").strip()
    filter_status = request.args.get("filter_status", "").strip()
    filter_user_id = request.args.get("filter_user_id", "").strip()
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()

    query = Sale.query

    if not is_admin:
        query = query.filter(Sale.user_id == user_id)
    else:
        if filter_user_id:
            try:
                uid = int(filter_user_id)
                query = query.filter(Sale.user_id == uid)
            except ValueError:
                pass

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))

    if filter_status:
        query = query.filter(Sale.status == filter_status)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date >= df)
        except ValueError:
            pass

    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date <= dt)
        except ValueError:
            pass

    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    total_ventas = len(sales)
    total_monto = sum(s.total or 0.0 for s in sales)
    total_ganancia = sum(s.profit or 0.0 for s in sales)
    total_pagado = sum(
        s.total or 0.0 for s in sales if (s.status or "").lower() == "pagado".lower()
    )
    total_pendiente = total_monto - total_pagado

    # Productos para el selector
    prod_query = Product.query
    if not is_admin:
        prod_query = prod_query.filter(Product.user_id == user_id)
    products = prod_query.order_by(Product.name.asc()).all()

    product_mapping = {
        p.name: {"cost": f"{p.cost:.2f}", "price": f"{p.price:.2f}"} for p in products
    }

    # Usuarios para filtro (solo admin)
    users = []
    if is_admin:
        users = User.query.order_by(User.username.asc()).all()

    # Clientes para selector
    client_query = Client.query
    if not is_admin:
        client_query = client_query.filter(Client.user_id == user_id)
    clients = client_query.order_by(Client.name.asc()).all()

    return render_template(
        "ventas.html",
        error=error,
        success=success,
        sales=sales,
        total_ventas=total_ventas,
        total_monto=total_monto,
        total_ganancia=total_ganancia,
        total_pagado=total_pagado,
        total_pendiente=total_pendiente,
        products=products,
        product_mapping=product_mapping,
        users=users,
        filter_name=filter_name,
        filter_status=filter_status,
        filter_user_id=filter_user_id,
        date_from=date_from_str,
        date_to=date_to_str,
        clients=clients,
    )


@app.route("/delete_sale/<int:sale_id>", methods=["POST"])
@login_required
def delete_sale(sale_id):
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    sale = Sale.query.get_or_404(sale_id)
    if not is_admin and sale.user_id != user_id:
        return redirect(url_for("ventas"))

    try:
        db.session.delete(sale)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(url_for("ventas"))


@app.route("/mark_sale_paid/<int:sale_id>", methods=["POST"])
@login_required
def mark_sale_paid(sale_id):
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    sale = Sale.query.get_or_404(sale_id)
    if not is_admin and sale.user_id != user_id:
        return redirect(url_for("ventas"))

    sale.status = "Pagado"
    sale.due_date = None
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(url_for("ventas"))


@app.route("/ventas_export")
@login_required
def ventas_export():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    filter_name = request.args.get("filter_name", "").strip()
    filter_status = request.args.get("filter_status", "").strip()
    filter_user_id = request.args.get("filter_user_id", "").strip()
    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()

    query = Sale.query

    if not is_admin:
        query = query.filter(Sale.user_id == user_id)
    else:
        if filter_user_id:
            try:
                uid = int(filter_user_id)
                query = query.filter(Sale.user_id == uid)
            except ValueError:
                pass

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))

    if filter_status:
        query = query.filter(Sale.status == filter_status)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date >= df)
        except ValueError:
            pass

    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date <= dt)
        except ValueError:
            pass

    sales = query.order_by(Sale.date.asc(), Sale.id.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Ventas"

    headers = [
        "ID",
        "Fecha",
        "Cliente",
        "Producto",
        "Estado",
        "Costo U",
        "Precio U",
        "Cantidad",
        "Total",
        "Ganancia",
        "Comentario",
        "Usuario",
        "Fecha pago (vencimiento)",
    ]
    ws.append(headers)

    for s in sales:
        username = s.user.username if getattr(s, "user", None) else ""
        ws.append(
            [
                s.id,
                s.date.isoformat() if s.date else "",
                s.name or "",
                s.product or "",
                s.status or "",
                s.cost_per_unit or 0.0,
                s.price_per_unit or 0.0,
                s.quantity or 0,
                s.total or 0.0,
                s.profit or 0.0,
                s.comment or "",
                username,
                s.due_date.isoformat() if s.due_date else "",
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"ventas_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# CONTROL DE FLUJO
# -----------------------------------------------------------------------------
@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    error = None
    success = None

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        category = request.form.get("category", "Gasto")
        description = request.form.get("description", "").strip()
        amount_str = request.form.get("amount", "0").replace(",", ".")

        try:
            if date_str:
                exp_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                exp_date = date.today()

            amount = float(amount_str)

            exp = Expense(
                date=exp_date,
                category=category,
                description=description,
                amount=amount,
                user_id=user_id,
            )
            db.session.add(exp)
            db.session.commit()
            success = "Movimiento de flujo registrado correctamente."
        except ValueError:
            error = "Monto inválido."
        except Exception as exc:
            db.session.rollback()
            error = f"Error al guardar el movimiento: {exc}"

    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    category_filter = request.args.get("category_filter", "").strip()

    exp_query = Expense.query
    if not is_admin:
        exp_query = exp_query.filter(Expense.user_id == user_id)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            exp_query = exp_query.filter(Expense.date >= df)
        except ValueError:
            pass

    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            exp_query = exp_query.filter(Expense.date <= dt)
        except ValueError:
            pass

    if category_filter:
        exp_query = exp_query.filter(Expense.category == category_filter)

    expenses = exp_query.order_by(Expense.date.desc(), Expense.id.desc()).all()

    # Cálculos
    total_gastos = sum(
        e.amount or 0.0 for e in expenses if (e.category or "") == "Gasto"
    )
    total_reinv = sum(
        e.amount or 0.0 for e in expenses if (e.category or "") == "Reinversión"
    )
    total_egresos = total_gastos + total_reinv

    sales_query = Sale.query
    if not is_admin:
        sales_query = sales_query.filter(Sale.user_id == user_id)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            sales_query = sales_query.filter(Sale.date >= df)
        except ValueError:
            pass

    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            sales_query = sales_query.filter(Sale.date <= dt)
        except ValueError:
            pass

    sales_period = sales_query.all()
    total_ingresos = sum(s.total or 0.0 for s in sales_period)
    total_ganancia = sum(s.profit or 0.0 for s in sales_period)

    ahorro_objetivo = total_ganancia * 0.10
    ahorro_real = max(total_ganancia - total_egresos, 0.0)
    ahorro_faltante = max(ahorro_objetivo - ahorro_real, 0.0)
    meta_cumplida = ahorro_real >= ahorro_objetivo and ahorro_objetivo > 0

    neto = total_ganancia - total_egresos

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
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    exp = Expense.query.get_or_404(expense_id)
    if not is_admin and exp.user_id != user_id:
        return redirect(url_for("flujo"))

    try:
        db.session.delete(exp)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return redirect(url_for("flujo"))


@app.route("/flujo_export")
@login_required
def flujo_export():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    category_filter = request.args.get("category_filter", "").strip()

    exp_query = Expense.query
    if not is_admin:
        exp_query = exp_query.filter(Expense.user_id == user_id)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            exp_query = exp_query.filter(Expense.date >= df)
        except ValueError:
            pass

    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            exp_query = exp_query.filter(Expense.date <= dt)
        except ValueError:
            pass

    if category_filter:
        exp_query = exp_query.filter(Expense.category == category_filter)

    expenses = exp_query.order_by(Expense.date.asc(), Expense.id.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Flujo"

    headers = [
        "ID",
        "Fecha",
        "Descripción",
        "Categoría",
        "Monto",
        "Usuario",
    ]
    ws.append(headers)

    for e in expenses:
        username = e.user.username if getattr(e, "user", None) else ""
        ws.append(
            [
                e.id,
                e.date.isoformat() if e.date else "",
                e.description or "",
                e.category or "",
                e.amount or 0.0,
                username,
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"flujo_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# -----------------------------------------------------------------------------
# DASHBOARD
# -----------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    date_from_str = request.args.get("date_from", "").strip()
    date_to_str = request.args.get("date_to", "").strip()
    preset = request.args.get("preset", "").strip()

    today = date.today()

    if preset:
        if preset == "week":
            df = today - timedelta(days=6)
            dt = today
        elif preset == "4weeks":
            df = today - timedelta(weeks=4)
            dt = today
        elif preset == "month":
            df = today.replace(day=1)
            dt = today
        elif preset == "year":
            df = today.replace(month=1, day=1)
            dt = today
        else:
            df = None
            dt = None

        if df and dt:
            date_from_str = df.isoformat()
            date_to_str = dt.isoformat()

    date_from = None
    date_to = None
    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            date_from = None
    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            date_to = None

    sales_query = Sale.query
    if not is_admin:
        sales_query = sales_query.filter(Sale.user_id == user_id)

    if date_from:
        sales_query = sales_query.filter(Sale.date >= date_from)
    if date_to:
        sales_query = sales_query.filter(Sale.date <= date_to)

    sales_list = sales_query.all()

    total_ganancia = sum(s.profit or 0.0 for s in sales_list)
    total_monto_period = sum(s.total or 0.0 for s in sales_list)
    total_ventas_period = len(sales_list)
    avg_ticket = total_monto_period / total_ventas_period if total_ventas_period > 0 else 0.0

    # Utilidad diaria promedio
    if date_from and date_to:
        days = (date_to - date_from).days + 1
        days = max(days, 1)
    else:
        days = 1
    avg_daily_profit = total_ganancia / days if days > 0 else 0.0

    # Top productos por ganancia
    product_profit = {}
    for s in sales_list:
        key = s.product or "Sin nombre"
        product_profit[key] = product_profit.get(key, 0.0) + (s.profit or 0.0)

    top_items = sorted(product_profit.items(), key=lambda x: x[1], reverse=True)[:5]
    top_labels = [name for name, _ in top_items]
    top_values = [val for _, val in top_items]

    # Ganancia por semana (ISO)
    weekly_profit = {}
    for s in sales_list:
        if not s.date:
            continue
        iso_year, iso_week, _ = s.date.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        weekly_profit[key] = weekly_profit.get(key, 0.0) + (s.profit or 0.0)

    week_labels = sorted(weekly_profit.keys())
    week_values = [weekly_profit[w] for w in week_labels]

    if week_values:
        max_weekly_profit = max(week_values)
        min_weekly_profit = min(week_values)
    else:
        max_weekly_profit = 0.0
        min_weekly_profit = 0.0

    # Ganancia por usuario (solo tiene sentido para admin)
    user_labels = []
    user_values = []
    if is_admin:
        user_profit_map = {}
        for s in sales_list:
            uname = s.user.username if getattr(s, "user", None) else "?"
            user_profit_map[uname] = user_profit_map.get(uname, 0.0) + (s.profit or 0.0)
        user_labels = list(user_profit_map.keys())
        user_values = list(user_profit_map.values())
    else:
        u = User.query.get(user_id)
        uname = u.username if u else "Yo"
        user_labels = [uname]
        user_values = [total_ganancia]

    # Pagos vencidos y próximos (solo pendientes con due_date)
    pend_query = Sale.query.filter(Sale.status == "Pendiente", Sale.due_date.isnot(None))
    if not is_admin:
        pend_query = pend_query.filter(Sale.user_id == user_id)

    pend_sales = pend_query.all()
    overdue_total = 0.0
    overdue_count = 0
    upcoming_total = 0.0
    upcoming_count = 0

    for s in pend_sales:
        if not s.due_date:
            continue
        if s.due_date < today:
            overdue_total += s.total or 0.0
            overdue_count += 1
        elif today <= s.due_date <= today + timedelta(days=7):
            upcoming_total += s.total or 0.0
            upcoming_count += 1

    # Alertas
    alerts = []

    if overdue_total > 0:
        alerts.append(
            {
                "level": "danger",
                "title": "Pagos vencidos",
                "message": f"Tienes <b>{overdue_count}</b> ventas pendientes con fecha de pago vencida por un total aproximado de <b>₡{format_num(overdue_total)}</b>. Revisa la pestaña Ventas y realiza gestiones de cobro.",
            }
        )

    if upcoming_total > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Pagos próximos a vencer",
                "message": f"En los próximos 7 días se vencen <b>{upcoming_count}</b> pagos pendientes por un total aproximado de <b>₡{format_num(upcoming_total)}</b>. Considera contactar a tus clientes con anticipación.",
            }
        )

    if total_ganancia <= 0 and total_ventas_period > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Ganancia baja",
                "message": "La ganancia del periodo es muy baja o negativa. Revisa tus márgenes y tus gastos en el apartado de Control de Flujo.",
            }
        )

    if not alerts and total_ventas_period > 0:
        alerts.append(
            {
                "level": "info",
                "title": "Operación estable",
                "message": "No se han detectado alertas críticas. Mantén tus registros al día para aprovechar mejor el análisis.",
            }
        )

    return render_template(
        "dashboard.html",
        date_from=date_from_str,
        date_to=date_to_str,
        alerts=alerts,
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
        max_weekly_profit=max_weekly_profit,
        min_weekly_profit=min_weekly_profit,
        overdue_total=overdue_total,
        overdue_count=overdue_count,
        upcoming_total=upcoming_total,
        upcoming_count=upcoming_count,
    )


# -----------------------------------------------------------------------------
# MAIN (para entorno local)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
