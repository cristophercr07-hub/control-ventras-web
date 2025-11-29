from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date
from io import BytesIO
import os
import openpyxl

# -----------------------------------------------------------------------------
# App & DB setup
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

database_url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    sales = db.relationship("Sale", backref="user", lazy=True)
    expenses = db.relationship("Expense", backref="user", lazy=True)
    products = db.relationship("Product", backref="user", lazy=True)
    clients = db.relationship("Client", backref="user", lazy=True)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(150))
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), default="Pagado")  # Pagado / Pendiente
    name = db.Column(db.String(150))  # Client name (texto plano)
    product = db.Column(db.String(150))
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)

    # Nuevos campos para pagos pendientes
    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date, nullable=True)

    comment = db.Column(db.String(250))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(200))
    category = db.Column(db.String(50))  # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


# -----------------------------------------------------------------------------
# DB bootstrap (Flask 3 compatible)
# -----------------------------------------------------------------------------

def bootstrap_db():
    db.create_all()

    # Crear usuario admin por defecto si no existe
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", is_admin=True)
        admin.set_password("admin")
        db.session.add(admin)
        db.session.commit()

with app.app_context():
    bootstrap_db()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def current_user():
    if "user_id" not in session:
        return None
    return User.query.get(session["user_id"])


@app.template_filter("format_num")
def format_num(value):
    """Formatea número como 1.234,56 (estilo más legible)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0,00"
    # miles con punto, decimales con coma
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# -----------------------------------------------------------------------------
# Rutas de autenticación
# -----------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
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
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            error = "Usuario y contraseña son obligatorios."
        else:
            if User.query.filter_by(username=username).first():
                error = "Ya existe un usuario con ese nombre."
            else:
                u = User(username=username, is_admin=is_admin)
                u.set_password(password)
                db.session.add(u)
                db.session.commit()
                success = "Usuario creado correctamente."

    users = User.query.order_by(User.username.asc()).all()
    return render_template("usuarios.html", error=error, success=success, users=users)


@app.route("/usuarios/delete/<int:user_id>", methods=["POST"])
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
# Clientes
# -----------------------------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
    user = current_user()
    error = None
    success = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        if not name:
            error = "El nombre del cliente es obligatorio."
        else:
            try:
                client = Client(name=name, phone=phone, email=email, notes=notes, user_id=user.id)
                db.session.add(client)
                db.session.commit()
                success = "Cliente guardado correctamente."
            except Exception as e:
                db.session.rollback()
                error = f"Error al guardar el cliente: {str(e)}"

    clients = Client.query.filter_by(user_id=user.id).order_by(Client.name.asc()).all()
    return render_template("clientes.html", error=error, success=success, clients=clients)


@app.route("/clientes/delete/<int:client_id>", methods=["POST"])
@login_required
def delete_client(client_id):
    user = current_user()
    client = Client.query.filter_by(id=client_id, user_id=user.id).first_or_404()
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes"))


@app.route("/clientes/export")
@login_required
def clientes_export():
    user = current_user()
    clients = Client.query.filter_by(user_id=user.id).order_by(Client.name.asc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Clientes"

    ws.append(["Nombre", "Teléfono", "Email", "Notas"])
    for c in clients:
        ws.append([c.name, c.phone or "", c.email or "", c.notes or ""])

    f = BytesIO()
    wb.save(f)
    f.seek(0)

    return send_file(
        f,
        as_attachment=True,
        download_name="clientes.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# -----------------------------------------------------------------------------
# Productos + Calculadora
# -----------------------------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
    user = current_user()
    error = None
    success = None

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

        # ---------------------------------------------------------
        # 1) Calculadora (en la misma página)
        # ---------------------------------------------------------
        if form_type == "calculator":
            product_name_input = (request.form.get("product_name") or "").strip()
            cost_input = request.form.get("cost") or "0"
            margin_input = request.form.get("margin") or "0"
            quantity_input = request.form.get("quantity") or "1"
            save_to_catalog = bool(request.form.get("save_to_catalog"))

            try:
                cost = float(cost_input)
                margin = float(margin_input)
                qty = int(quantity_input)
            except ValueError:
                error = "Verifica los campos numéricos."
            else:
                if cost < 0 or qty <= 0:
                    error = "El costo debe ser >= 0 y la cantidad >= 1."
                else:
                    price = cost * (1.0 + margin / 100.0)
                    price_result = price
                    profit_unit = price - cost
                    profit_total = profit_unit * qty
                    margin_used = margin

                    if save_to_catalog and product_name_input:
                        existing = Product.query.filter_by(
                            user_id=user.id, name=product_name_input
                        ).first()
                        if existing:
                            existing.cost = cost
                            existing.price = price
                            existing.margin_percent = margin
                            success = "Producto actualizado en el catálogo."
                        else:
                            p = Product(
                                name=product_name_input,
                                cost=cost,
                                price=price,
                                margin_percent=margin,
                                user_id=user.id,
                            )
                            db.session.add(p)
                            success = "Producto guardado en el catálogo."
                        db.session.commit()

        # ---------------------------------------------------------
        # 2) Formulario directo de catálogo
        # ---------------------------------------------------------
        elif form_type == "catalog":
            name = (request.form.get("name") or "").strip()
            cost_raw = request.form.get("cost") or "0"
            price_raw = request.form.get("price") or "0"

            try:
                cost = float(cost_raw)
                price = float(price_raw)
            except ValueError:
                error = "Verifica los campos numéricos en el catálogo."
            else:
                if not name:
                    error = "El nombre del producto es obligatorio."
                else:
                    margin = 0.0
                    if cost > 0:
                        margin = ((price - cost) / cost) * 100.0

                    existing = Product.query.filter_by(user_id=user.id, name=name).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price
                        existing.margin_percent = margin
                        success = "Producto actualizado correctamente."
                    else:
                        p = Product(
                            name=name,
                            cost=cost,
                            price=price,
                            margin_percent=margin,
                            user_id=user.id,
                        )
                        db.session.add(p)
                        success = "Producto creado correctamente."
                    db.session.commit()

    products = Product.query.filter_by(user_id=user.id).order_by(Product.name.asc()).all()

    return render_template(
        "productos.html",
        error=error,
        success=success,
        products=products,
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
    user = current_user()
    product = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos"))


@app.route("/productos/export")
@login_required
def productos_export():
    user = current_user()
    products = Product.query.filter_by(user_id=user.id).order_by(Product.name.asc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Productos"

    ws.append(["Nombre", "Costo", "Precio", "Margen (%)"])
    for p in products:
        ws.append([p.name, p.cost or 0.0, p.price or 0.0, p.margin_percent or 0.0])

    f = BytesIO()
    wb.save(f)
    f.seek(0)

    return send_file(
        f,
        as_attachment=True,
        download_name="productos.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# -----------------------------------------------------------------------------
# Ventas
# -----------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    if "user_id" in session:
        return redirect(url_for("ventas"))
    return redirect(url_for("login"))


@app.route("/ventas", methods=["GET", "POST"])
@login_required
def ventas():
    user = current_user()
    error = None
    success = None

    if request.method == "POST":
        # Eliminar venta
        if "delete_sale" in request.form:
            sale_id = int(request.form.get("delete_sale"))
            sale = Sale.query.filter_by(id=sale_id, user_id=user.id).first()
            if sale:
                db.session.delete(sale)
                db.session.commit()
                success = "Venta eliminada correctamente."
            else:
                error = "No se encontró la venta."
        else:
            # Registrar / actualizar venta
            date_str = request.form.get("date")
            status = request.form.get("status") or "Pagado"
            client_id_raw = request.form.get("client_id") or ""
            client_name_manual = (request.form.get("name") or "").strip()

            product_select = request.form.get("product_select") or ""
            product_input = (request.form.get("product") or "").strip()

            cost_raw = request.form.get("cost_per_unit") or "0"
            price_raw = request.form.get("price_per_unit") or "0"
            quantity_raw = request.form.get("quantity") or "1"
            comment = (request.form.get("comment") or "").strip()

            pending_amount_raw = request.form.get("pending_amount") or "0"
            due_date_str = request.form.get("due_date") or ""

            try:
                cost = float(cost_raw)
                price = float(price_raw)
                quantity = int(quantity_raw)
                pending_amount = float(pending_amount_raw)
            except ValueError:
                error = "Verifica los campos numéricos."
            else:
                if not date_str:
                    error = "La fecha es obligatoria."
                elif quantity <= 0:
                    error = "La cantidad debe ser mayor o igual a 1."
                else:
                    try:
                        sale_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        sale_date = date.today()

                    # Determinar nombre del cliente (texto)
                    client_name_final = client_name_manual
                    client_id = None
                    if client_id_raw:
                        try:
                            client_id = int(client_id_raw)
                        except ValueError:
                            client_id = None
                        if client_id:
                            c = Client.query.filter_by(id=client_id, user_id=user.id).first()
                            if c:
                                client_name_final = c.name

                    # Determinar nombre del producto
                    product_name_final = product_input or product_select

                    total = price * quantity
                    profit = (price - cost) * quantity

                    due_date = None
                    if due_date_str:
                        try:
                            due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                        except ValueError:
                            due_date = None

                    if status == "Pagado":
                        pending_amount = 0.0

                    sale = Sale(
                        date=sale_date,
                        status=status,
                        name=client_name_final,
                        product=product_name_final,
                        cost_per_unit=cost,
                        price_per_unit=price,
                        quantity=quantity,
                        total=total,
                        profit=profit,
                        pending_amount=pending_amount,
                        due_date=due_date,
                        comment=comment,
                        user_id=user.id,
                    )
                    db.session.add(sale)
                    db.session.commit()
                    success = "Venta registrada correctamente."

    # filtros GET
    filter_name = (request.args.get("filter_name") or "").strip()
    filter_status = request.args.get("filter_status") or ""
    filter_user_id = request.args.get("filter_user_id") or ""
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""

    query = Sale.query.filter_by(user_id=user.id)

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))
    if filter_status:
        query = query.filter(Sale.status == filter_status)

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date >= d_from)
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date <= d_to)
        except ValueError:
            pass

    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    total_ventas = len(sales)
    total_monto = sum(s.total or 0 for s in sales)
    total_ganancia = sum(s.profit or 0 for s in sales)
    total_pagado = sum((s.total or 0) for s in sales if s.status == "Pagado")
    total_pendiente = sum((s.pending_amount or 0) for s in sales if s.status == "Pendiente")

    products = Product.query.filter_by(user_id=user.id).order_by(Product.name.asc()).all()
    clients = Client.query.filter_by(user_id=user.id).order_by(Client.name.asc()).all()

    product_mapping = {
        p.name: {"cost": round(p.cost or 0, 2), "price": round(p.price or 0, 2)}
        for p in products
    }

    users = []
    if session.get("is_admin"):
        users = User.query.order_by(User.username.asc()).all()

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
        filter_name=filter_name,
        filter_status=filter_status,
        filter_user_id=filter_user_id,
        date_from=date_from_str,
        date_to=date_to_str,
        products=products,
        product_mapping=product_mapping,
        clients=clients,
        users=users,
    )


@app.route("/ventas/delete/<int:sale_id>", methods=["POST"])
@login_required
def delete_sale(sale_id):
    user = current_user()
    sale = Sale.query.filter_by(id=sale_id, user_id=user.id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("ventas"))


@app.route("/ventas/mark_paid/<int:sale_id>", methods=["POST"])
@login_required
def mark_sale_paid(sale_id):
    user = current_user()
    sale = Sale.query.filter_by(id=sale_id, user_id=user.id).first_or_404()
    sale.status = "Pagado"
    sale.pending_amount = 0.0
    sale.due_date = None
    db.session.commit()
    return redirect(url_for("ventas"))


@app.route("/ventas/export")
@login_required
def ventas_export():
    user = current_user()

    filter_name = (request.args.get("filter_name") or "").strip()
    filter_status = request.args.get("filter_status") or ""
    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""

    query = Sale.query.filter_by(user_id=user.id)

    if filter_name:
        query = query.filter(Sale.name.ilike(f"%{filter_name}%"))
    if filter_status:
        query = query.filter(Sale.status == filter_status)

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date >= d_from)
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            query = query.filter(Sale.date <= d_to)
        except ValueError:
            pass

    sales = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ventas"

    ws.append([
        "Fecha", "Cliente", "Producto", "Estado",
        "Costo U.", "Precio U.", "Cantidad", "Total",
        "Ganancia", "Monto pendiente", "Fecha compromiso", "Comentario"
    ])

    for s in sales:
        ws.append([
            s.date.isoformat() if s.date else "",
            s.name or "",
            s.product or "",
            s.status or "",
            s.cost_per_unit or 0.0,
            s.price_per_unit or 0.0,
            s.quantity or 0,
            s.total or 0.0,
            s.profit or 0.0,
            s.pending_amount or 0.0,
            s.due_date.isoformat() if s.due_date else "",
            s.comment or "",
        ])

    f = BytesIO()
    wb.save(f)
    f.seek(0)

    return send_file(
        f,
        as_attachment=True,
        download_name="ventas.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# -----------------------------------------------------------------------------
# Flujo de caja
# -----------------------------------------------------------------------------

@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
    user = current_user()
    error = None
    success = None

    if request.method == "POST":
        date_str = request.form.get("date")
        category = request.form.get("category") or "Gasto"
        description = (request.form.get("description") or "").strip()
        amount_raw = request.form.get("amount") or "0"

        try:
            amount = float(amount_raw)
        except ValueError:
            error = "El monto debe ser numérico."
        else:
            if not date_str:
                error = "La fecha es obligatoria."
            elif amount < 0:
                error = "El monto debe ser mayor o igual a 0."
            else:
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    d = date.today()

                e = Expense(
                    date=d,
                    category=category,
                    description=description,
                    amount=amount,
                    user_id=user.id,
                )
                db.session.add(e)
                db.session.commit()
                success = "Movimiento registrado correctamente."

    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    q = Expense.query.filter_by(user_id=user.id)

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            q = q.filter(Expense.date >= d_from)
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            q = q.filter(Expense.date <= d_to)
        except ValueError:
            pass
    if category_filter:
        q = q.filter(Expense.category == category_filter)

    expenses = q.order_by(Expense.date.desc(), Expense.id.desc()).all()

    sales_q = Sale.query.filter_by(user_id=user.id)
    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            sales_q = sales_q.filter(Sale.date >= d_from)
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            sales_q = sales_q.filter(Sale.date <= d_to)
        except ValueError:
            pass

    sales_for_period = sales_q.all()

    total_ingresos = sum(s.total or 0 for s in sales_for_period)
    total_ganancia = sum(s.profit or 0 for s in sales_for_period)
    total_gastos = sum(e.amount or 0 for e in expenses if e.category == "Gasto")
    total_reinv = sum(e.amount or 0 for e in expenses if e.category == "Reinversión")
    total_egresos = total_gastos + total_reinv
    neto = total_ganancia - total_egresos

    ahorro_objetivo = total_ganancia * 0.10
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
    user = current_user()
    exp = Expense.query.filter_by(id=expense_id, user_id=user.id).first_or_404()
    db.session.delete(exp)
    db.session.commit()
    return redirect(url_for("flujo"))


@app.route("/flujo/export")
@login_required
def flujo_export():
    user = current_user()

    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    q = Expense.query.filter_by(user_id=user.id)

    if date_from_str:
        try:
            d_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
            q = q.filter(Expense.date >= d_from)
        except ValueError:
            pass
    if date_to_str:
        try:
            d_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
            q = q.filter(Expense.date <= d_to)
        except ValueError:
            pass
    if category_filter:
        q = q.filter(Expense.category == category_filter)

    expenses = q.order_by(Expense.date.desc(), Expense.id.desc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Flujo"

    ws.append(["Fecha", "Descripción", "Tipo", "Monto"])
    for e in expenses:
        ws.append([
            e.date.isoformat() if e.date else "",
            e.description or "",
            e.category or "",
            e.amount or 0.0,
        ])

    f = BytesIO()
    wb.save(f)
    f.seek(0)

    return send_file(
        f,
        as_attachment=True,
        download_name="flujo.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    date_from_str = request.args.get("date_from") or ""
    date_to_str = request.args.get("date_to") or ""
    preset = request.args.get("preset") or ""

    today = date.today()

    if preset == "week":
        start = today.fromordinal(today.toordinal() - 6)
        end = today
    elif preset == "4weeks":
        start = today.fromordinal(today.toordinal() - 27)
        end = today
    elif preset == "month":
        start = today.replace(day=1)
        end = today
    elif preset == "year":
        start = today.replace(month=1, day=1)
        end = today
    else:
        start = None
        end = None

    if date_from_str:
        try:
            start = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_to_str:
        try:
            end = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if start is None:
        start = today.replace(day=1)
    if end is None:
        end = today

    sales_q = Sale.query.filter_by(user_id=user.id).filter(Sale.date >= start, Sale.date <= end)
    sales_period = sales_q.all()

    total_monto_period = sum(s.total or 0 for s in sales_period)
    total_ganancia = sum(s.profit or 0 for s in sales_period)
    total_ventas_period = len(sales_period)

    days_diff = (end - start).days + 1
    avg_daily_profit = total_ganancia / days_diff if days_diff > 0 else 0.0
    avg_ticket = total_monto_period / total_ventas_period if total_ventas_period > 0 else 0.0

    # Top productos
    product_profit = {}
    for s in sales_period:
        if not s.product:
            continue
        product_profit.setdefault(s.product, 0.0)
        product_profit[s.product] += s.profit or 0.0

    sorted_products = sorted(product_profit.items(), key=lambda x: x[1], reverse=True)[:6]
    top_labels = [p[0] for p in sorted_products]
    top_values = [round(p[1], 2) for p in sorted_products]

    # Ganancia por semana ISO
    weekly_profit = {}
    for s in sales_period:
        if not s.date:
            continue
        year, week, _ = s.date.isocalendar()
        key = f"{year}-W{week:02d}"
        weekly_profit.setdefault(key, 0.0)
        weekly_profit[key] += s.profit or 0.0

    week_labels = sorted(weekly_profit.keys())
    week_values = [round(weekly_profit[w], 2) for w in week_labels]

    # Ganancia por usuario (para un solo usuario actual)
    user_profit = {user.username: total_ganancia}
    user_labels = list(user_profit.keys())
    user_values = [round(v, 2) for v in user_profit.values()]

    # Alertas: pagos pendientes
    today_dt = date.today()
    overdue_sales = Sale.query.filter_by(user_id=user.id, status="Pendiente") \
        .filter(Sale.due_date < today_dt).all()
    upcoming_sales = Sale.query.filter_by(user_id=user.id, status="Pendiente") \
        .filter(Sale.due_date >= today_dt).all()

    overdue_total = sum(s.pending_amount or 0 for s in overdue_sales)
    upcoming_total = sum(s.pending_amount or 0 for s in upcoming_sales)
    overdue_count = len(overdue_sales)
    upcoming_count = len(upcoming_sales)

    alerts = []

    if overdue_total > 0:
        alerts.append({
            "level": "danger",
            "title": "Pagos vencidos",
            "message": f"Tienes {overdue_count} ventas con pagos vencidos por un total de ₡{format_num(overdue_total)}."
        })
    if upcoming_total > 0:
        alerts.append({
            "level": "warning",
            "title": "Pagos próximos",
            "message": f"Hay {upcoming_count} ventas pendientes con pagos próximos por ₡{format_num(upcoming_total)}."
        })

    return render_template(
        "dashboard.html",
        date_from=start.isoformat(),
        date_to=end.isoformat(),
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
    )


# -----------------------------------------------------------------------------
# API sencilla para obtener datos de producto
# -----------------------------------------------------------------------------

@app.route("/api/product/<int:product_id>")
@login_required
def api_product(product_id):
    user = current_user()
    p = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    return jsonify({
        "id": p.id,
        "name": p.name,
        "cost": round(p.cost or 0, 2),
        "price": round(p.price or 0, 2),
        "margin_percent": round(p.margin_percent or 0, 2),
    })


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
