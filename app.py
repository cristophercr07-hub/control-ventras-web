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
    jsonify,
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

# Configuración de BD: Postgres en producción, SQLite en local
database_url = os.environ.get("DATABASE_URL", "").strip()

if database_url:
    # Render suele entregar postgres:// y SQLAlchemy requiere postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # Fallback local a SQLite (archivo ventas.db en la carpeta del proyecto)
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(BASE_DIR, "ventas.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Cookies de sesión más seguras
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

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


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    notes = db.Column(db.String(255))

    # Dueño del cliente (multiusuario)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    owner = db.relationship("User", backref="clients", lazy=True)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)

    # Dueño del producto (multiusuario)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    owner = db.relationship("User", backref="products", lazy=True)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    # Ahora se referencia a Client, pero mantenemos name/product de texto para histórico
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)
    client = db.relationship("Client", backref="sales", lazy=True)

    name = db.Column(db.String(120), nullable=False)      # nombre del cliente (histórico)
    product = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False)     # Pagado / Pendiente
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)
    comment = db.Column(db.String(255))

    # Usuario del sistema que registró la venta (dueño de la venta)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user = db.relationship("User", backref="sales", lazy=True)

    # Fecha de vencimiento de pago (para ventas pendientes)
    payment_due_date = db.Column(db.Date, nullable=True)
    payment_reminder_sent = db.Column(db.Boolean, default=False)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(20), nullable=False)   # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)

    # Dueño del gasto (multiusuario)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    owner = db.relationship("User", backref="expenses", lazy=True)


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


def current_user_id():
    return session.get("user_id")


def current_is_admin():
    return bool(session.get("is_admin"))


def user_scope_query(model):
    """
    Devuelve un query filtrado por usuario para multiusuario:
      - Admin: ve todo.
      - Usuario normal:
          * Sale: solo sus ventas (Sale.user_id == su id)
          * Product/Client/Expense: solo registros con owner_id == su id
    """
    q = model.query
    uid = current_user_id()
    if not uid:
        return q

    if current_is_admin():
        return q

    if hasattr(model, "owner_id"):
        return q.filter(model.owner_id == uid)

    if model is Sale:
        return q.filter(Sale.user_id == uid)

    return q


# ---------------------------------------------------------
# INICIALIZACIÓN DE LA BD Y MIGRACIONES BÁSICAS
# ---------------------------------------------------------

with app.app_context():
    db.create_all()

    inspector = inspect(db.engine)

    # Asegurar columnas en SALE (instalaciones anteriores)
    try:
        sale_cols = [col["name"] for col in inspector.get_columns("sale")]

        if "user_id" not in sale_cols:
            db.session.execute("ALTER TABLE sale ADD COLUMN user_id INTEGER")

        if "payment_due_date" not in sale_cols:
            db.session.execute("ALTER TABLE sale ADD COLUMN payment_due_date DATE")

        if "payment_reminder_sent" not in sale_cols:
            db.session.execute(
                "ALTER TABLE sale ADD COLUMN payment_reminder_sent BOOLEAN DEFAULT 0"
            )

        if "client_id" not in sale_cols:
            db.session.execute("ALTER TABLE sale ADD COLUMN client_id INTEGER")

        db.session.commit()
    except Exception:
        db.session.rollback()

    # Asegurar columnas owner_id en PRODUCT, EXPENSE, CLIENT (multiusuario)
    try:
        # PRODUCT
        prod_cols = [col["name"] for col in inspector.get_columns("product")]
        if "owner_id" not in prod_cols:
            db.session.execute("ALTER TABLE product ADD COLUMN owner_id INTEGER")

        # EXPENSE
        exp_cols = [col["name"] for col in inspector.get_columns("expense")]
        if "owner_id" not in exp_cols:
            db.session.execute("ALTER TABLE expense ADD COLUMN owner_id INTEGER")

        # CLIENT
        client_cols = [col["name"] for col in inspector.get_columns("client")]
        if "owner_id" not in client_cols:
            db.session.execute("ALTER TABLE client ADD COLUMN owner_id INTEGER")

        db.session.commit()
    except Exception:
        db.session.rollback()

    # Crear o actualizar usuario admin usando variables de entorno
    admin_username = os.environ.get("ADMIN_USER", "admin")
    admin_password = os.environ.get("ADMIN_PASS")  # si no está, se usa fallback (solo dev)

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

    # Asignar registros antiguos sin dueño al admin (si existe)
    admin_obj = User.query.filter_by(is_admin=True).first()
    if admin_obj:
        # Productos sin owner -> admin
        Product.query.filter(Product.owner_id.is_(None)).update(
            {"owner_id": admin_obj.id}, synchronize_session=False
        )
        # Gastos sin owner -> admin
        Expense.query.filter(Expense.owner_id.is_(None)).update(
            {"owner_id": admin_obj.id}, synchronize_session=False
        )
        # Clientes sin owner -> admin
        Client.query.filter(Client.owner_id.is_(None)).update(
            {"owner_id": admin_obj.id}, synchronize_session=False
        )
        # Ventas sin user_id -> admin
        Sale.query.filter(Sale.user_id.is_(None)).update(
            {"user_id": admin_obj.id}, synchronize_session=False
        )
        db.session.commit()


# ---------------------------------------------------------
# FILTRO GLOBAL PARA FORMATEAR NÚMEROS
# ---------------------------------------------------------

@app.template_filter("format_num")
def format_num(value):
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    # Separador de miles con coma, luego cambiamos coma por punto
    s = f"{n:,.2f}"
    s = s.replace(",", ".")
    return s


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
        is_admin_flag = bool(request.form.get("is_admin"))

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
                    is_admin=is_admin_flag,
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
# CLIENTES
# ---------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
def clientes():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    error = None
    success = None
    uid = current_user_id()

    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            notes = (request.form.get("notes") or "").strip()

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")

            # Buscamos dentro del alcance del usuario
            existing = user_scope_query(Client).filter_by(name=name).first()
            if existing:
                existing.phone = phone
                existing.email = email
                existing.notes = notes
                success = "Cliente actualizado correctamente."
            else:
                c = Client(
                    name=name,
                    phone=phone,
                    email=email,
                    notes=notes,
                    owner_id=uid,
                )
                db.session.add(c)
                success = "Cliente creado correctamente."

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            error = f"Error al guardar el cliente: {e}"

    clients = user_scope_query(Client).order_by(Client.name).all()

    return render_template(
        "clientes.html",
        clients=clients,
        error=error,
        success=success,
    )


@app.post("/clientes/<int:client_id>/delete")
def delete_client(client_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    uid = current_user_id()
    is_admin_flag = current_is_admin()

    client = Client.query.get_or_404(client_id)

    if (not is_admin_flag) and client.owner_id != uid:
        return redirect(url_for("clientes"))

    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


# ---------------------------------------------------------
# PRODUCTOS + CALCULADORA (FUSIONADOS)
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
def productos():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    error = None
    success = None
    uid = current_user_id()

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
                    # Buscar dentro del alcance del usuario
                    existing = user_scope_query(Product).filter_by(
                        name=product_name_input
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
                            owner_id=uid,
                        )
                        db.session.add(p)
                    db.session.commit()
                    success = "Producto guardado/actualizado en el catálogo."
            except Exception as e:
                db.session.rollback()
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

                existing = user_scope_query(Product).filter_by(name=name).first()
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
                        owner_id=uid,
                    )
                    db.session.add(p)
                    db.session.commit()
                    success = "Producto creado correctamente."
            except Exception as e:
                db.session.rollback()
                error = f"Error al guardar el producto: {e}"

    products = user_scope_query(Product).order_by(Product.name).all()
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

    uid = current_user_id()
    is_admin_flag = current_is_admin()

    product = Product.query.get_or_404(product_id)

    if (not is_admin_flag) and product.owner_id != uid:
        return redirect(url_for("productos"))

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
    uid = current_user_id()

    if request.method == "POST":
        try:
            date_str = request.form.get("date")
            date = parse_date(date_str) or datetime.date.today()

            client_id_str = request.form.get("client_id") or ""
            client_obj = None
            if client_id_str:
                try:
                    client_id_val = int(client_id_str)
                    # Cliente dentro del alcance del usuario
                    client_obj = user_scope_query(Client).filter_by(id=client_id_val).first()
                except ValueError:
                    client_obj = None

            name = ""
            if client_obj:
                name = client_obj.name
            else:
                name = (request.form.get("name") or "").strip()

            product_from_select = (request.form.get("product_select") or "").strip()
            product_input = (request.form.get("product") or "").strip()
            product = product_input or product_from_select

            status = request.form.get("status") or "Pagado"
            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)
            quantity = int(request.form.get("quantity") or 1)
            comment = (request.form.get("comment") or "").strip()

            payment_due_str = request.form.get("payment_due_date") or ""
            payment_due_date = parse_date(payment_due_str)

            if not name:
                raise ValueError("El nombre del cliente es obligatorio.")
            if not product:
                raise ValueError("Debes seleccionar o escribir un producto.")
            if quantity <= 0:
                raise ValueError("La cantidad debe ser mayor que cero.")

            if status == "Pendiente" and not payment_due_date:
                raise ValueError("Debes indicar una fecha de pago para las ventas pendientes.")

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            sale = Sale(
                date=date,
                client_id=client_obj.id if client_obj else None,
                name=name,
                product=product,
                status=status,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                quantity=quantity,
                total=total,
                profit=profit,
                comment=comment,
                user_id=uid,
                payment_due_date=payment_due_date if status == "Pendiente" else None,
                payment_reminder_sent=False,
            )
            db.session.add(sale)
            db.session.commit()
            success = "Venta guardada correctamente."
        except Exception as e:
            db.session.rollback()
            error = f"Error al guardar la venta: {e}"

    # Filtros (GET)
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    filter_user_id = request.args.get("filter_user_id") or ""

    # Base query limitada por usuario
    query = user_scope_query(Sale)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)

    # Filtro por usuario solo tiene sentido para admin
    if filter_user_id and current_is_admin():
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

    products = user_scope_query(Product).order_by(Product.name).all()
    users = User.query.order_by(User.username).all()  # Para el filtro (solo útil a admin)
    clients = user_scope_query(Client).order_by(Client.name).all()

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
        clients=clients,
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

    uid = current_user_id()
    is_admin_flag = current_is_admin()

    sale = Sale.query.get_or_404(sale_id)

    if (not is_admin_flag) and sale.user_id != uid:
        return redirect(url_for("ventas", success="No tienes permiso para eliminar esa venta."))

    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas", success="Venta eliminada correctamente."))


@app.post("/ventas/<int:sale_id>/paid")
def mark_sale_paid(sale_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    uid = current_user_id()
    is_admin_flag = current_is_admin()

    sale = Sale.query.get_or_404(sale_id)

    if (not is_admin_flag) and sale.user_id != uid:
        return redirect(url_for("ventas", success="No tienes permiso para modificar esa venta."))

    sale.status = "Pagado"
    sale.payment_due_date = None
    sale.payment_reminder_sent = False
    db.session.commit()

    return redirect(url_for("ventas", success="Venta marcada como pagada."))


@app.route("/ventas/export")
def ventas_export():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    filter_user_id = request.args.get("filter_user_id") or ""

    query = user_scope_query(Sale)
    query = apply_sales_filters(query, filter_name, filter_status, date_from, date_to)

    if filter_user_id and current_is_admin():
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
    uid = current_user_id()

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
                owner_id=uid,
            )
            db.session.add(e)
            db.session.commit()
            success = "Movimiento registrado correctamente."
        except Exception as e:
            db.session.rollback()
            error = f"Error al registrar movimiento: {e}"

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = user_scope_query(Expense)
    sales_query = user_scope_query(Sale)

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


@app.post("/flujo/<int:expense_id>/delete")
def delete_expense(expense_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    uid = current_user_id()
    is_admin_flag = current_is_admin()

    expense = Expense.query.get_or_404(expense_id)

    if (not is_admin_flag) and expense.owner_id != uid:
        return redirect(url_for("flujo"))

    db.session.delete(expense)
    db.session.commit()
    return redirect(url_for("flujo"))


@app.route("/flujo/export")
def flujo_export():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    exp_query = user_scope_query(Expense)

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

    # Base query limitada por usuario
    sales_query = user_scope_query(Sale)

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

    avg_ticket = (
        total_monto_period / total_ventas_period if total_ventas_period > 0 else 0.0
    )
    avg_profit_per_sale = (
        total_ganancia / total_ventas_period if total_ventas_period > 0 else 0.0
    )

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

    # Ganancia por usuario del sistema (solo tendrá sentido de verdad para admin;
    # para usuario normal normalmente verá solo su usuario)
    profit_by_user = defaultdict(float)
    for s in sales:
        if s.user:
            profit_by_user[s.user.username] += float(s.profit or 0)

    user_items = sorted(profit_by_user.items(), key=lambda x: x[1], reverse=True)
    user_labels = [u for u, _ in user_items]
    user_values = [round(v, 2) for _, v in user_items]

    # -------------------------------------------------
    # ALERTAS AUTOMÁTICAS (a nivel del alcance del usuario)
    # -------------------------------------------------
    alerts = []

    today = datetime.date.today()

    # 1) Ventas pendientes vencidas (filtradas por usuario)
    pending_sales = user_scope_query(Sale).filter(Sale.status == "Pendiente").all()
    old_pending = []
    upcoming_pending = []

    for s in pending_sales:
        d = s.payment_due_date or s.date
        if not d:
            continue
        if isinstance(d, datetime.datetime):
            d = d.date()
        if isinstance(d, str):
            try:
                d = datetime.date.fromisoformat(d)
            except Exception:
                continue

        if d < today:
            old_pending.append(s)
        elif d <= today + datetime.timedelta(days=2):
            upcoming_pending.append(s)

    if old_pending:
        total_pend_antiguo = sum(float(s.total or 0) for s in old_pending)
        alerts.append({
            "level": "danger",
            "title": "Pagos vencidos",
            "message": (
                f"Tienes {len(old_pending)} ventas pendientes con fecha de pago vencida "
                f"por un monto total aproximado de ₡{format_num(total_pend_antiguo)}. "
                "Revisa los cobros atrasados."
            ),
        })

    if upcoming_pending and not old_pending:
        total_pend_prox = sum(float(s.total or 0) for s in upcoming_pending)
        alerts.append({
            "level": "warning",
            "title": "Pagos próximos a vencer",
            "message": (
                f"En los próximos 2 días se vencen {len(upcoming_pending)} ventas "
                f"por un monto de aproximadamente ₡{format_num(total_pend_prox)}. "
                "Anticípate y coordina los cobros."
            ),
        })

    # 2) Utilidad semanal por debajo de un umbral objetivo (limitada al usuario)
    seven_days_ago = today - datetime.timedelta(days=7)
    try:
        weekly_sales = (
            user_scope_query(Sale)
            .filter(Sale.date >= seven_days_ago, Sale.date <= today)
            .all()
        )
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
                f"La utilidad de los últimos 7 días es de ₡{format_num(weekly_profit)}, "
                f"por debajo del objetivo mínimo de ₡{format_num(min_weekly_profit)}. "
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
# API AUXILIAR PARA SPA (si la usas, por ejemplo para recargar charts)
# ---------------------------------------------------------

@app.route("/api/ventas/productos-mapping")
def api_product_mapping():
    if not session.get("user_id"):
        return jsonify({"error": "not_authenticated"}), 401

    products = user_scope_query(Product).order_by(Product.name).all()
    mapping = {
        p.name: {"cost": float(p.cost or 0), "price": float(p.price or 0)} for p in products
    }
    return jsonify(mapping)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
