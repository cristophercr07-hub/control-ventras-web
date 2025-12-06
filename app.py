\import os
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

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# ---------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = "cambiar_esto_por_un_valor_mas_seguro"

db_path = os.path.join(BASE_DIR, "database.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Cambia a False en producción
app.config["DEBUG"] = True

db = SQLAlchemy(app)


# ---------------------------------------------------------
# CONSTANTES / CONFIGURACIONES
# ---------------------------------------------------------

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

    def check_password(self, password_plain: str) -> bool:
        return check_password_hash(self.password_hash, password_plain)


class Client(db.Model):
    """
    Modelo de clientes para asociar ventas a clientes registrados.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    notes = db.Column(db.String(255))


class Product(db.Model):
    """
    Modelo de productos para catálogo de precios.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255))
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)


class Sale(db.Model):
    """
    Modelo de ventas principales.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    date = db.Column(db.Date, default=datetime.date.today, nullable=False)
    name = db.Column(db.String(120), nullable=False)  # Nombre del cliente
    product = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="Pagado")  # Pagado o Pendiente
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)
    payment_type = db.Column(db.String(50), default="Contado")
    amount_paid = db.Column(db.Float, default=0.0)
    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date, nullable=True)  # Fecha de vencimiento
    notes = db.Column(db.String(255))  # Comentarios
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)

    user = db.relationship("User", backref=db.backref("sales", lazy=True))
    client = db.relationship("Client", backref=db.backref("sales", lazy=True))


class Expense(db.Model):
    """
    Modelo de gastos para control de flujo.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    date = db.Column(db.Date, default=datetime.date.today, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, default=0.0)

    user = db.relationship("User", backref=db.backref("expenses", lazy=True))


# Crear todas las tablas si no existen (después de definir los modelos)
with app.app_context():
    db.create_all()


# ---------------------------------------------------------
# FILTROS JINJA PERSONALIZADOS
# ---------------------------------------------------------

@app.template_filter("format_num")
def format_num(value):
    """
    Formatea números con separador de miles y 2 decimales (formato latino).
    Ejemplo: 12345.6 -> '12.345,60'
    """
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return "0,00"
    # 12,345.67 -> 12.345,67
    s = f"{value:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


# ---------------------------------------------------------
# FUNCIONES AUXILIARES Y DECORADORES
# ---------------------------------------------------------

def login_required(f):
    """
    Decorador para requerir que el usuario esté autenticado.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


def current_user():
    """
    Devuelve el usuario actual logueado o None.
    """
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def parse_date(date_str: str):
    """
    Parsea fechas en formato YYYY-MM-DD. Devuelve None si falla.
    """
    if not date_str:
        return None
    try:
        year, month, day = map(int, date_str.split("-"))
        return datetime.date(year, month, day)
    except Exception:
        return None


def query_for(model):
    """
    Helper para hacer query ordenada por ID descendente (últimos primero).
    """
    return db.session.query(model).order_by(model.id.desc())


# ---------------------------------------------------------
# RUTAS DE AUTENTICACIÓN
# ---------------------------------------------------------

@app.route("/init_admin")
def init_admin():
    """
    Crea un usuario admin por defecto (admin / admin) si no existen usuarios.
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
    Usar solo para inicialización; luego se puede eliminar o proteger.
    """
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(
            username="admin",
            is_admin=True,
            password_hash=generate_password_hash("admin"),
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
# RUTAS PRINCIPALES
# ---------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    """
    Dashboard principal con resumen de ventas y gastos.
    """
    user = current_user()
    today = datetime.date.today()
    first_day_month = today.replace(day=1)

    sales = query_for(Sale).filter(Sale.date >= first_day_month).all()
    expenses = query_for(Expense).filter(Expense.date >= first_day_month).all()

    total_sales = sum(s.total or 0 for s in sales)
    total_profit = sum(s.profit or 0 for s in sales)
    total_expenses = sum(e.amount or 0 for e in expenses)

    balance = total_profit - total_expenses

    # Agrupación diaria para gráficos
    daily_sales = defaultdict(float)
    daily_profit = defaultdict(float)
    daily_expenses = defaultdict(float)

    for s in sales:
        daily_sales[s.date] += s.total or 0
        daily_profit[s.date] += s.profit or 0

    for e in expenses:
        daily_expenses[e.date] += e.amount or 0

    # Fechas ordenadas
    date_list = sorted(set(list(daily_sales.keys()) + list(daily_expenses.keys())))
    chart_labels = [d.strftime("%d-%m") for d in date_list]
    chart_sales = [round(daily_sales[d], 2) for d in date_list]
    chart_profit = [round(daily_profit[d], 2) for d in date_list]
    chart_expenses = [round(daily_expenses[d], 2) for d in date_list]

    # Ventas recientes
    recent_sales = query_for(Sale).limit(10).all()

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
    )


# ---------------------------------------------------------
# RUTAS DE USUARIOS (ADMIN)
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
# RUTAS DE CLIENTES
# ---------------------------------------------------------

@app.route("/clientes", methods=["GET", "POST"])
@login_required
def clientes():
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

    # Filtro de nombre
    filter_name = request.args.get("filter_name") or ""
    query = query_for(Client)
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
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clientes", success="Cliente eliminado correctamente."))


# ---------------------------------------------------------
# RUTAS DE PRODUCTOS
# ---------------------------------------------------------

@app.route("/productos", methods=["GET", "POST"])
@login_required
def productos():
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

            existing = Product.query.filter_by(name=name).first()
            if existing:
                raise ValueError("Ya existe un producto con ese nombre.")

            product = Product(
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

    # Filtro de nombre
    filter_name = request.args.get("filter_name") or ""
    query = query_for(Product)
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
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for("productos", success="Producto eliminado correctamente."))


# ---------------------------------------------------------
# RUTA DE VENTAS
# ---------------------------------------------------------

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

            # Cálculos base
            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            # Ajuste de montos según el estado de la venta
            if status == "Pagado":
                # Si la venta es Pagada y no se indicó monto, asumimos que se pagó todo
                if amount_paid <= 0:
                    amount_paid = total
                pending_amount = 0.0
            else:
                # Venta pendiente: lo pendiente es total - lo pagado
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

    # Filtros de búsqueda
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    query = query_for(Sale)

    if filter_name:
        like_pattern = f"%{filter_name}%"
        query = query.filter(Sale.name.ilike(like_pattern))

    if filter_status:
        query = query.filter_by(status=filter_status)

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    if d_from:
        query = query.filter(Sale.date >= d_from)
    if d_to:
        query = query.filter(Sale.date <= d_to)

    sales = query.all()

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


@app.post("/ventas/<int:sale_id>/update_amount_paid")
@login_required
def update_sale_amount_paid(sale_id):
    q = query_for(Sale)
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

    # Si ya no hay saldo pendiente (o es muy pequeño), marcamos como Pagado
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
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    query = query_for(Sale)

    if filter_name:
        like_pattern = f"%{filter_name}%"
        query = query.filter(Sale.name.ilike(like_pattern))

    if filter_status:
        query = query.filter_by(status=filter_status)

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    if d_from:
        query = query.filter(Sale.date >= d_from)
    if d_to:
        query = query.filter(Sale.date <= d_to)

    sales = query.all()

    # Exportar como CSV simple
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
# RUTAS DE FLUJO (INGRESOS / GASTOS)
# ---------------------------------------------------------

@app.route("/flujo", methods=["GET", "POST"])
@login_required
def flujo():
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
                user_id=current_user().id,
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

    # Filtros
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

    expenses = exp_query.all()
    sales = sales_query.all()

    total_expenses = sum(e.amount or 0 for e in expenses)
    total_sales = sum(s.total or 0 for s in sales)
    total_profit = sum(s.profit or 0 for s in sales)
    balance = total_profit - total_expenses

    # Agrupación por categoría
    category_totals = defaultdict(float)
    for e in expenses:
        category_totals[e.category] += e.amount or 0

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
    e = Expense.query.get_or_404(expense_id)
    db.session.delete(e)
    db.session.commit()
    return redirect(url_for("flujo", success="Movimiento eliminado correctamente."))


# ---------------------------------------------------------
# CALCULADORA DE PRECIOS (MÁRGENES, COSTOS, ETC.)
# ---------------------------------------------------------

@app.route("/calculadora", methods=["GET", "POST"])
@login_required
def calculadora():
    error = None
    result = None

    if request.method == "POST":
        try:
            # Modo de cálculo: "price_from_cost" o "cost_from_price"
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
                    existing = query_for(Product).filter_by(
                        name=product_name_input
                    ).first()
                    if existing:
                        existing.cost = cost
                        existing.price = price_result
                    else:
                        new_product = Product(
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
                # cost_from_price
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
                    existing = query_for(Product).filter_by(
                        name=product_name_input
                    ).first()
                    if existing:
                        existing.cost = cost_result
                        existing.price = price
                    else:
                        new_product = Product(
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

    products = query_for(Product).order_by(Product.name).all()
    return render_template(
        "calculadora.html",
        error=error,
        result=result,
        products=products,
        min_margin_percent=MIN_MARGIN_PERCENT,
    )


# ---------------------------------------------------------
# API AUXILIAR PARA OBTENER DATOS DE UN PRODUCTO
# ---------------------------------------------------------

@app.route("/api/product/<int:product_id>")
@login_required
def api_product(product_id):
    product = Product.query.get_or_404(product_id)
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
# ERROR HANDLERS SIMPLES
# ---------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html", error=str(e)), 500


# ---------------------------------------------------------
# MAIN (para ejecución local)
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
