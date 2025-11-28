import os
from datetime import datetime, date
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_file,
    Response,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# -----------------------------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# -----------------------------------------------------------------------------
app = Flask(__name__)

# Clave secreta
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

# Base de datos: primero intenta con DATABASE_URL (Render / producción)
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Render suele dar una URL tipo postgres://, la convertimos a postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # Local: SQLite
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///control_ventas.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# -----------------------------------------------------------------------------
# FILTRO DE FORMATEO DE NÚMEROS
# -----------------------------------------------------------------------------
def format_num(value):
    """
    Formatea números con separador de miles y 2 decimales, estilo '1.234.567,89'.
    Si no se puede convertir, devuelve el valor original en string.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    # Formato básico: 1,234,567.89
    formatted = f"{number:,.2f}"

    # Cambiar a formato con punto de miles y coma de decimales
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return formatted


app.jinja_env.filters["format_num"] = format_num


# -----------------------------------------------------------------------------
# MODELOS
# -----------------------------------------------------------------------------
class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    sales = db.relationship("Sale", back_populates="user", lazy=True)
    expenses = db.relationship("Expense", back_populates="user", lazy=True)
    products = db.relationship("Product", back_populates="user", lazy=True)
    clients = db.relationship("Client", back_populates="user", lazy=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    __tablename__ = "client"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    user = db.relationship("User", back_populates="clients")
    sales = db.relationship("Sale", back_populates="client", lazy=True)


class Product(db.Model):
    __tablename__ = "product"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    user = db.relationship("User", back_populates="products")


class Sale(db.Model):
    __tablename__ = "sale"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), default="Pagado")  # Pagado / Pendiente
    name = db.Column(db.String(120))  # nombre de cliente plano
    product = db.Column(db.String(120))
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)

    payment_due_date = db.Column(db.Date, nullable=True)
    payment_reminder_sent = db.Column(db.Boolean, default=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", back_populates="sales")

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)
    client = db.relationship("Client", back_populates="sales")


class Expense(db.Model):
    __tablename__ = "expense"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # Gasto / Reinversión
    amount = db.Column(db.Float, nullable=False, default=0.0)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", back_populates="expenses")


# -----------------------------------------------------------------------------
# UTILERÍAS
# -----------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapper


def init_db():
    """Inicializa la base de datos y crea el usuario admin si no existe."""
    db.create_all()

    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(username="admin", is_admin=True)
        admin.set_password("admin")
        db.session.add(admin)
        db.session.commit()


# Llamamos a init_db una vez al cargar el módulo (compatible con Flask 3.x)
with app.app_context():
    init_db()


# -----------------------------------------------------------------------------
# AUTENTICACIÓN
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            error = "Usuario o contraseña incorrectos."
        else:
            session["user_id"] = user.id
            session["user"] = user.username
            session["is_admin"] = user.is_admin
            return redirect(url_for("ventas"))

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# GESTIÓN DE USUARIOS (solo admin)
# -----------------------------------------------------------------------------
@app.route("/usuarios", methods=["GET", "POST"])
@login_required
def usuarios():
    if not session.get("is_admin"):
        return redirect(url_for("ventas"))

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
                user = User(username=username, is_admin=is_admin)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                success = "Usuario creado correctamente."

    users = User.query.order_by(User.username).all()
    return render_template("usuarios.html", users=users, error=error, success=success)


@app.route("/usuarios/delete/<int:user_id>", methods=["POST"])
@login_required
def delete_user(user_id):
    if not session.get("is_admin"):
        return redirect(url_for("ventas"))

    if session.get("user_id") == user_id:
        # No se puede eliminar a sí mismo
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
    error = None
    success = None
    current_user_id = session.get("user_id")

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        if not name:
            error = "El nombre del cliente es obligatorio."
        else:
            try:
                existing = Client.query.filter_by(name=name).first()
                if existing:
                    existing.phone = phone
                    existing.email = email
                    existing.notes = notes
                    existing.user_id = current_user_id
                    success = "Cliente actualizado correctamente."
                else:
                    new_client = Client(
                        name=name,
                        phone=phone,
                        email=email,
                        notes=notes,
                        user_id=current_user_id,
                    )
                    db.session.add(new_client)
                    success = "Cliente creado correctamente."

                db.session.commit()
            except Exception as e:
                db.session.rollback()
                error = f"Error al guardar el cliente: {e}"

    # Mostrar TODOS los clientes (global)
    clients = Client.query.order_by(Client.name).all()
    return render_template("clientes.html", clients=clients, error=error, success=success)


@app.route("/clientes/delete/<int:client_id>", methods=["POST"])
@login_required
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


# -----------------------------------------------------------------------------
# PRODUCTOS + CALCULADORA
# -----------------------------------------------------------------------------
@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    error = None
    success = None
    current_user_id = session.get("user_id")

    min_margin = 20.0  # margen mínimo recomendado

    product_name_input = ""
    cost_input = ""
    margin_input = ""
    quantity_input = ""
    price_result = None
    profit_unit = None
    profit_total = None
    margin_used = None

    if request.method == "POST":
        form_type = request.form.get("form_type") or "calculator"

        if form_type == "calculator":
            product_name_input = (request.form.get("product_name") or "").strip()
            cost_input = request.form.get("cost") or "0"
            margin_input = request.form.get("margin") or "0"
            quantity_input = request.form.get("quantity") or "1"
            save_to_catalog = bool(request.form.get("save_to_catalog"))

            try:
                cost = float(cost_input)
            except ValueError:
                cost = 0.0

            try:
                margin_percent = float(margin_input)
            except ValueError:
                margin_percent = 0.0

            try:
                quantity = int(quantity_input)
            except ValueError:
                quantity = 1

            if margin_percent < min_margin:
                error = (
                    f"El margen mínimo recomendado es {min_margin:.1f}%. "
                    f"Estás usando {margin_percent:.1f}%."
                )

            if cost <= 0:
                error = "Debes ingresar un costo mayor a 0."

            if not error:
                price_result = cost * (1 + margin_percent / 100.0)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity
                margin_used = margin_percent

                if save_to_catalog and product_name_input:
                    try:
                        existing = Product.query.filter_by(name=product_name_input).first()
                        if existing:
                            existing.cost = cost
                            existing.price = price_result
                            existing.margin_percent = margin_percent
                            existing.user_id = current_user_id
                            success = "Producto actualizado en el catálogo."
                        else:
                            new_product = Product(
                                name=product_name_input,
                                cost=cost,
                                price=price_result,
                                margin_percent=margin_percent,
                                user_id=current_user_id,
                            )
                            db.session.add(new_product)
                            success = "Producto guardado en el catálogo."
                        db.session.commit()
                    except Exception as e:
                        db.session.rollback()
                        error = f"Error al guardar en el catálogo: {e}"

        elif form_type == "catalog":
            # Guardar / actualizar producto directamente
            name = (request.form.get("name") or "").strip()
            cost_str = request.form.get("cost") or "0"
            price_str = request.form.get("price") or "0"

            if not name:
                error = "El nombre del producto es obligatorio."
            else:
                try:
                    cost = float(cost_str)
                except ValueError:
                    cost = 0.0
                try:
                    price = float(price_str)
                except ValueError:
                    price = 0.0

                margin_percent = 0.0
                if cost > 0:
                    margin_percent = ((price - cost) / cost) * 100.0

                try:
                    existing = Product.query.filter_by(name=name).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price
                        existing.margin_percent = margin_percent
                        existing.user_id = current_user_id
                        success = "Producto actualizado correctamente."
                    else:
                        new_product = Product(
                            name=name,
                            cost=cost,
                            price=price,
                            margin_percent=margin_percent,
                            user_id=current_user_id,
                        )
                        db.session.add(new_product)
                        success = "Producto creado correctamente."
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    error = f"Error al guardar el producto: {e}"

    products = Product.query.order_by(Product.name).all()
    return render_template(
        "productos.html",
        products=products,
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
    )


@app.route("/productos/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos"))


# -----------------------------------------------------------------------------
# VENTAS
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("ventas"))


@app.route("/ventas", methods=["GET", "POST"])
@login_required
def ventas():
    error = None
    success = None
    current_user_id = session.get("user_id")

    # -------------------------------------------------------------------------
    # CREAR VENTA (POST)
    # -------------------------------------------------------------------------
    if request.method == "POST":
        date_str = request.form.get("date") or ""
        status = request.form.get("status") or "Pagado"
        client_id_str = request.form.get("client_id") or ""
        name = (request.form.get("name") or "").strip()
        product_name = (request.form.get("product") or "").strip()
        cost_per_unit_str = request.form.get("cost_per_unit") or "0"
        price_per_unit_str = request.form.get("price_per_unit") or "0"
        quantity_str = request.form.get("quantity") or "1"
        comment = (request.form.get("comment") or "").strip()
        payment_due_str = request.form.get("payment_due_date") or ""

        try:
            sale_date = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else date.today()
            )
        except ValueError:
            sale_date = date.today()

        try:
            cost_per_unit = float(cost_per_unit_str)
        except ValueError:
            cost_per_unit = 0.0

        try:
            price_per_unit = float(price_per_unit_str)
        except ValueError:
            price_per_unit = 0.0

        try:
            quantity = int(quantity_str)
        except ValueError:
            quantity = 1

        payment_due_date = None
        if payment_due_str:
            try:
                payment_due_date = datetime.strptime(
                    payment_due_str, "%Y-%m-%d"
                ).date()
            except ValueError:
                payment_due_date = None

        total = price_per_unit * quantity
        profit = (price_per_unit - cost_per_unit) * quantity

        try:
            sale = Sale(
                date=sale_date,
                status=status,
                name=name,
                product=product_name,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                quantity=quantity,
                total=total,
                profit=profit,
                user_id=current_user_id,
                payment_due_date=payment_due_date,
            )

            if client_id_str:
                try:
                    sale.client_id = int(client_id_str)
                except ValueError:
                    sale.client_id = None

            # 'comment' no está en la base de datos; se podría usar en el futuro
            sale.comment = comment  # atributo dinámico para la instancia actual

            db.session.add(sale)
            db.session.commit()
            success = "Venta registrada correctamente."
        except Exception as e:
            db.session.rollback()
            error = f"Error al guardar la venta: {e}"

    # -------------------------------------------------------------------------
    # FILTROS (GET)
    # -------------------------------------------------------------------------
    filter_name = (request.args.get("filter_name") or "").strip()
    filter_status = request.args.get("filter_status") or ""
    filter_user_id = request.args.get("filter_user_id") or ""
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""

    # Cada usuario ve solo sus ventas
    query = Sale.query.filter_by(user_id=current_user_id)

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))
    if filter_status:
        query = query.filter_by(status=filter_status)
    if filter_user_id:
        try:
            uid = int(filter_user_id)
            query = query.filter_by(user_id=uid)
        except ValueError:
            pass

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

    sales = query.order_by(Sale.date.desc()).all()

    total_ventas = len(sales)
    total_monto = sum(s.total or 0 for s in sales)
    total_ganancia = sum(s.profit or 0 for s in sales)
    total_pagado = sum((s.total or 0) for s in sales if s.status == "Pagado")
    total_pendiente = total_monto - total_pagado

    # Usuarios para filtro
    users = User.query.order_by(User.username).all()

    # Productos catálogo para autocompletar
    products = Product.query.order_by(Product.name).all()
    product_mapping = {
        p.name: {"cost": float(p.cost or 0), "price": float(p.price or 0)} for p in products
    }

    # Clientes para selector (todos, globales)
    clients = Client.query.order_by(Client.name).all()

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


@app.route("/ventas/delete/<int:sale_id>", methods=["POST"])
@login_required
def delete_sale(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    if sale.user_id != session.get("user_id") and not session.get("is_admin"):
        return redirect(url_for("ventas"))
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas"))


@app.route("/ventas/mark_paid/<int:sale_id>", methods=["POST"])
@login_required
def mark_sale_paid(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    if sale.user_id != session.get("user_id") and not session.get("is_admin"):
        return redirect(url_for("ventas"))

    sale.status = "Pagado"
    sale.payment_due_date = None
    sale.payment_reminder_sent = False
    db.session.commit()
    return redirect(url_for("ventas"))


# -----------------------------------------------------------------------------
# FLUJO DE CAJA (GASTOS / REINVERSIÓN)
# -----------------------------------------------------------------------------
@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
    error = None
    success = None
    current_user_id = session.get("user_id")

    # Registrar movimiento
    if request.method == "POST":
        date_str = request.form.get("date") or ""
        category = request.form.get("category") or "Gasto"
        description = (request.form.get("description") or "").strip()
        amount_str = request.form.get("amount") or "0"

        if not description:
            error = "La descripción es obligatoria."
        else:
            try:
                amount = float(amount_str)
            except ValueError:
                amount = 0.0

            try:
                mov_date = (
                    datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str
                    else date.today()
                )
            except ValueError:
                mov_date = date.today()

            try:
                exp = Expense(
                    date=mov_date,
                    description=description,
                    category=category,
                    amount=amount,
                    user_id=current_user_id,
                )
                db.session.add(exp)
                db.session.commit()
                success = "Movimiento registrado correctamente."
            except Exception as e:
                db.session.rollback()
                error = f"Error al guardar el movimiento: {e}"

    # Filtros
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    q_exp = Expense.query.filter_by(user_id=current_user_id)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            q_exp = q_exp.filter(Expense.date >= df)
        except ValueError:
            pass
    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            q_exp = q_exp.filter(Expense.date <= dt)
        except ValueError:
            pass
    if category_filter:
        q_exp = q_exp.filter_by(category=category_filter)

    expenses = q_exp.order_by(Expense.date.desc()).all()

    # Resumen ingresos / ganancia desde ventas (mismo usuario)
    q_sales = Sale.query.filter_by(user_id=current_user_id)
    total_ingresos = sum(s.total or 0 for s in q_sales)
    total_ganancia = sum(s.profit or 0 for s in q_sales)

    total_gastos = sum(e.amount or 0 for e in expenses if e.category == "Gasto")
    total_reinv = sum(e.amount or 0 for e in expenses if e.category == "Reinversión")
    total_egresos = total_gastos + total_reinv
    neto = total_ganancia - total_egresos

    # Ahorro objetivo = 10% de la ganancia
    ahorro_objetivo = total_ganancia * 0.10
    # Suponemos ahorro_real = ganancia - egresos
    ahorro_real = max(0.0, total_ganancia - total_egresos)
    ahorro_faltante = max(0.0, ahorro_objetivo - ahorro_real)
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


@app.route("/flujo/delete/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    exp = Expense.query.get_or_404(expense_id)
    if exp.user_id != session.get("user_id") and not session.get("is_admin"):
        return redirect(url_for("flujo"))
    db.session.delete(exp)
    db.session.commit()
    return redirect(url_for("flujo"))


# -----------------------------------------------------------------------------
# EXPORTS (VENTAS Y FLUJO)
# -----------------------------------------------------------------------------
@app.route("/ventas/export")
@login_required
def ventas_export():
    current_user_id = session.get("user_id")

    filter_name = (request.args.get("filter_name") or "").strip()
    filter_status = request.args.get("filter_status") or ""
    filter_user_id = request.args.get("filter_user_id") or ""
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""

    query = Sale.query.filter_by(user_id=current_user_id)

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))
    if filter_status:
        query = query.filter_by(status=filter_status)
    if filter_user_id:
        try:
            uid = int(filter_user_id)
            query = query.filter_by(user_id=uid)
        except ValueError:
            pass

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

    sales = query.order_by(Sale.date.asc()).all()

    # CSV simple
    lines = ["Fecha,Cliente,Producto,Estado,CostoU,PrecioU,Cant,Total,Ganancia"]
    for s in sales:
        lines.append(
            f"{s.date.isoformat() if s.date else ''},"
            f"{s.name or ''},"
            f"{s.product or ''},"
            f"{s.status or ''},"
            f"{s.cost_per_unit or 0},"
            f"{s.price_per_unit or 0},"
            f"{s.quantity or 0},"
            f"{s.total or 0},"
            f"{s.profit or 0}"
        )

    csv_data = "\n".join(lines)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=ventas.csv"},
    )


@app.route("/flujo/export")
@login_required
def flujo_export():
    current_user_id = session.get("user_id")
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    q = Expense.query.filter_by(user_id=current_user_id)

    if date_from_str:
        try:
            df = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            q = q.filter(Expense.date >= df)
        except ValueError:
            pass
    if date_to_str:
        try:
            dt = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            q = q.filter(Expense.date <= dt)
        except ValueError:
            pass
    if category_filter:
        q = q.filter_by(category=category_filter)

    expenses = q.order_by(Expense.date.asc()).all()

    lines = ["Fecha,Descripción,Tipo,Monto"]
    for e in expenses:
        lines.append(
            f"{e.date.isoformat() if e.date else ''},"
            f"{e.description or ''},"
            f"{e.category or ''},"
            f"{e.amount or 0}"
        )

    csv_data = "\n".join(lines)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=flujo.csv"},
    )


# -----------------------------------------------------------------------------
# DASHBOARD
# -----------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    current_user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)

    # Filtros de fechas
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""
    preset = request.args.get("preset") or ""

    today = date.today()

    if preset == "week":
        # últimos 7 días
        dt_to = today
        dt_from = today.fromordinal(today.toordinal() - 6)
    elif preset == "4weeks":
        dt_to = today
        dt_from = today.fromordinal(today.toordinal() - 27)
    elif preset == "month":
        dt_from = today.replace(day=1)
        dt_to = today
    elif preset == "year":
        dt_from = date(today.year, 1, 1)
        dt_to = today
    else:
        dt_from = None
        dt_to = None

    if date_from_str:
        try:
            dt_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_to_str:
        try:
            dt_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Base query: si admin, todas; si no, solo las del usuario
    if is_admin:
        q = Sale.query
    else:
        q = Sale.query.filter_by(user_id=current_user_id)

    if dt_from:
        q = q.filter(Sale.date >= dt_from)
    if dt_to:
        q = q.filter(Sale.date <= dt_to)

    sales = q.all()

    total_ganancia = sum(s.profit or 0 for s in sales)
    total_monto_period = sum(s.total or 0 for s in sales)
    total_ventas_period = len(sales)

    # Ticket promedio
    avg_ticket = (total_monto_period / total_ventas_period) if total_ventas_period else 0.0

    # Utilidad diaria promedio
    if dt_from and dt_to:
        days = (dt_to - dt_from).days + 1
    elif dt_from or dt_to:
        days = 1
    else:
        days = 1

    avg_daily_profit = (total_ganancia / days) if days > 0 else 0.0

    # Top productos por ganancia
    product_profit = {}
    for s in sales:
        key = s.product or "sin nombre"
        product_profit[key] = product_profit.get(key, 0.0) + (s.profit or 0.0)

    top_items = sorted(product_profit.items(), key=lambda x: x[1], reverse=True)[:5]
    top_labels = [t[0] for t in top_items]
    top_values = [round(t[1], 2) for t in top_items]

    # Ganancia por semana (ISO semana)
    weekly = {}
    for s in sales:
        if not s.date:
            continue
        year, week_num, _ = s.date.isocalendar()
        key = f"{year}-W{week_num:02d}"
        weekly[key] = weekly.get(key, 0.0) + (s.profit or 0.0)

    week_items = sorted(weekly.items(), key=lambda x: x[0])
    week_labels = [w[0] for w in week_items]
    week_values = [round(w[1], 2) for w in week_items]

    # Ganancia por usuario
    if is_admin:
        user_profit_map = {}
        for s in sales:
            uname = s.user.username if s.user else "desconocido"
            user_profit_map[uname] = user_profit_map.get(uname, 0.0) + (s.profit or 0.0)
        user_labels = list(user_profit_map.keys())
        user_values = [round(v, 2) for v in user_profit_map.values()]
    else:
        user_labels = [session.get("user")]
        user_values = [round(total_ganancia, 2)]

    # Alertas sencillas
    alerts = []

    if total_ventas_period == 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Sin ventas",
                "message": "No has registrado ventas en el rango seleccionado.",
            }
        )

    # Ventas pendientes para tarjetas de cobro
    if is_admin:
        base_q_pending = Sale.query.filter(Sale.status == "Pendiente")
    else:
        base_q_pending = Sale.query.filter(
            Sale.status == "Pendiente", Sale.user_id == current_user_id
        )

    overdue_sales = base_q_pending.filter(
        Sale.payment_due_date != None,  # noqa: E711
        Sale.payment_due_date < today,
    ).all()

    upcoming_sales = base_q_pending.filter(
        Sale.payment_due_date != None,  # noqa: E711
        Sale.payment_due_date >= today,
    ).all()

    overdue_total = sum(s.total or 0 for s in overdue_sales)
    overdue_count = len(overdue_sales)
    upcoming_total = sum(s.total or 0 for s in upcoming_sales)
    upcoming_count = len(upcoming_sales)

    if overdue_count > 0:
        alerts.append(
            {
                "level": "danger",
                "title": "Cobranzas vencidas",
                "message": f"Tienes {overdue_count} ventas pendientes con fecha de pago vencida.",
            }
        )

    return render_template(
        "dashboard.html",
        date_from=dt_from.isoformat() if dt_from else "",
        date_to=dt_to.isoformat() if dt_to else "",
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
        overdue_count=overdue_count,
        upcoming_total=upcoming_total,
        upcoming_count=upcoming_count,
    )


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
