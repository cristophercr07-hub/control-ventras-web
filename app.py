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

# Compatibilidad con Render (postgres:// → postgresql://)
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
    reference = db.Column(db.String(80))
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(250))
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)
    stock = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    identifier = db.Column(db.String(50))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    address = db.Column(db.String(250))
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    sales = db.relationship("Sale", backref="client", lazy=True)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(20), default="Pagado")  # Pagado / Pendiente
    name = db.Column(db.String(150))  # nombre cliente (texto)
    product = db.Column(db.String(150))
    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)

    payment_type = db.Column(db.String(50), default="Contado")
    amount_paid = db.Column(db.Float, default=0.0)
    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date, nullable=True)

    notes = db.Column(db.Text)

    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class DailyFlow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)

    initial_custody = db.Column(db.Float, default=0.0)
    initial_capital = db.Column(db.Float, default=0.0)
    initial_external_accounts = db.Column(db.Float, default=0.0)

    gross_profit = db.Column(db.Float, default=0.0)
    taxes_paid = db.Column(db.Float, default=0.0)
    expenses_paid = db.Column(db.Float, default=0.0)
    reinvestment = db.Column(db.Float, default=0.0)
    custody_used_for_fxd = db.Column(db.Float, default=0.0)
    capital_for_fxd = db.Column(db.Float, default=0.0)

    final_custody = db.Column(db.Float, default=0.0)
    final_capital = db.Column(db.Float, default=0.0)
    final_external_accounts = db.Column(db.Float, default=0.0)

    updated_capital = db.Column(db.Float, default=0.0)
    updated_custody = db.Column(db.Float, default=0.0)
    updated_external_accounts = db.Column(db.Float, default=0.0)

    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(250))
    category = db.Column(db.String(50))  # Gasto / Reinversión / Otro
    amount = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


# -----------------------------------------------------------------------------
# DB bootstrap (se ejecuta al arrancar la app)
# -----------------------------------------------------------------------------


def bootstrap_db():
    """Create tables and default admin if not present."""
    with app.app_context():
        db.create_all()
        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", is_admin=True)
            admin.set_password("admin")
            db.session.add(admin)
            db.session.commit()


bootstrap_db()


# Ruta manual para forzar la creación de tablas en la BD actual (Render)
@app.route("/init-db-force")
def init_db_force():
    """Crea todas las tablas y un usuario admin/admin si no existe."""
    with app.app_context():
        db.create_all()
        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", is_admin=True)
            admin.set_password("admin")
            db.session.add(admin)
            db.session.commit()
    return "Base de datos inicializada. Usuario admin/admin creado si no existía."


# -----------------------------------------------------------------------------
# Helpers & decorators
# -----------------------------------------------------------------------------


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


# Hacer disponible current_user() en todos los templates Jinja
@app.context_processor
def inject_current_user():
    return dict(current_user=current_user)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)

    return wrapper


def parse_date(s: str, default=None):
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return default


def format_num(value) -> str:
    if value is None:
        value = 0
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


app.jinja_env.filters["format_num"] = format_num

# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Usuario o contraseña incorrectos")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.username.asc()).all()
    return render_template("usuarios.html", users=all_users)


@app.route("/users/add", methods=["POST"])
@admin_required
def add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    is_admin = bool(request.form.get("is_admin"))

    if not username or not password:
        return render_template(
            "usuarios.html",
            users=User.query.order_by(User.username.asc()).all(),
            error="Usuario y contraseña son obligatorios",
        )

    if User.query.filter_by(username=username).first():
        return render_template(
            "usuarios.html",
            users=User.query.order_by(User.username.asc()).all(),
            error="Ya existe un usuario con ese nombre",
        )

    user = User(username=username, is_admin=is_admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return redirect(url_for("users"))


@app.route("/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        return redirect(url_for("users"))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("users"))


# -----------------------------------------------------------------------------
# Clients
# -----------------------------------------------------------------------------


@app.route("/clients")
@login_required
def clients():
    user = current_user()
    q = request.args.get("q", "").strip()
    query = Client.query.filter_by(user_id=user.id)
    if q:
        like_q = f"%{q}%"
        query = query.filter(
            (Client.name.ilike(like_q))
            | (Client.identifier.ilike(like_q))
            | (Client.phone.ilike(like_q))
            | (Client.email.ilike(like_q))
        )
    clients_list = query.order_by(Client.name.asc()).all()
    return render_template("clientes.html", clients=clients_list, q=q)


@app.route("/clients/add", methods=["POST"])
@login_required
def add_client():
    user = current_user()

    name = request.form.get("name", "").strip()
    identifier = request.form.get("identifier", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    notes = request.form.get("notes", "").strip()

    if not name:
        return render_template(
            "clientes.html",
            clients=Client.query.filter_by(user_id=user.id).all(),
            error="El nombre del cliente es obligatorio",
        )

    client = Client(
        name=name,
        identifier=identifier,
        phone=phone,
        email=email,
        address=address,
        notes=notes,
        user_id=user.id,
    )
    db.session.add(client)
    db.session.commit()
    return redirect(url_for("clients"))


@app.route("/clients/edit/<int:client_id>", methods=["POST"])
@login_required
def edit_client(client_id):
    user = current_user()
    client = Client.query.filter_by(id=client_id, user_id=user.id).first_or_404()

    client.name = request.form.get("name", "").strip()
    client.identifier = request.form.get("identifier", "").strip()
    client.phone = request.form.get("phone", "").strip()
    client.email = request.form.get("email", "").strip()
    client.address = request.form.get("address", "").strip()
    client.notes = request.form.get("notes", "").strip()

    db.session.commit()
    return redirect(url_for("clients"))


@app.route("/clients/delete/<int:client_id>", methods=["POST"])
@login_required
def delete_client(client_id):
    user = current_user()
    client = Client.query.filter_by(id=client_id, user_id=user.id).first_or_404()
    db.session.delete(client)
    db.session.commit()
    return redirect(url_for("clients"))


# -----------------------------------------------------------------------------
# Products
# -----------------------------------------------------------------------------


@app.route("/products")
@login_required
def products():
    user = current_user()
    q = request.args.get("q", "").strip()
    query = Product.query.filter_by(user_id=user.id)
    if q:
        like_q = f"%{q}%"
        query = query.filter(
            (Product.name.ilike(like_q))
            | (Product.reference.ilike(like_q))
            | (Product.description.ilike(like_q))
        )
    products_list = query.order_by(Product.name.asc()).all()
    return render_template("productos.html", products=products_list, q=q)


@app.route("/products/add", methods=["POST"])
@login_required
def add_product():
    user = current_user()
    name = request.form.get("name", "").strip()
    reference = request.form.get("reference", "").strip()
    description = request.form.get("description", "").strip()
    cost = float(request.form.get("cost") or 0)
    price = float(request.form.get("price") or 0)
    stock = int(request.form.get("stock") or 0)
    notes = request.form.get("notes", "").strip()

    margin_percent = 0.0
    if cost > 0:
        margin_percent = (price - cost) / cost * 100.0

    product = Product(
        name=name,
        reference=reference,
        description=description,
        cost=cost,
        price=price,
        stock=stock,
        margin_percent=margin_percent,
        notes=notes,
        user_id=user.id,
    )
    db.session.add(product)
    db.session.commit()
    return redirect(url_for("products"))


@app.route("/products/edit/<int:product_id>", methods=["POST"])
@login_required
def edit_product(product_id):
    user = current_user()
    p = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()

    p.name = request.form.get("name", "").strip()
    p.reference = request.form.get("reference", "").strip()
    p.description = request.form.get("description", "").strip()
    p.cost = float(request.form.get("cost") or 0)
    p.price = float(request.form.get("price") or 0)
    p.stock = int(request.form.get("stock") or 0)
    p.notes = request.form.get("notes", "").strip()

    if p.cost > 0:
        p.margin_percent = (p.price - p.cost) / p.cost * 100.0
    else:
        p.margin_percent = 0.0

    db.session.commit()
    return redirect(url_for("products"))


@app.route("/products/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    user = current_user()
    p = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    db.session.delete(p)
    db.session.commit()
    return redirect(url_for("products"))


# -----------------------------------------------------------------------------
# Sales
# -----------------------------------------------------------------------------


@app.route("/sales")
@login_required
def sales():
    user = current_user()
    date_from = parse_date(request.args.get("date_from"), None)
    date_to = parse_date(request.args.get("date_to"), None)
    status = request.args.get("status", "")

    query = Sale.query.filter_by(user_id=user.id)

    if date_from:
        query = query.filter(Sale.date >= date_from)
    if date_to:
        query = query.filter(Sale.date <= date_to)
    if status:
        query = query.filter(Sale.status == status)

    sales_list = query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    total_sales = sum(s.total or 0 for s in sales_list)
    total_profit = sum(s.profit or 0 for s in sales_list)

    clients_list = Client.query.filter_by(user_id=user.id).order_by(Client.name.asc()).all()
    products_list = Product.query.filter_by(user_id=user.id).order_by(Product.name.asc()).all()

    return render_template(
        "ventas.html",
        sales=sales_list,
        total_sales=total_sales,
        total_profit=total_profit,
        date_from=date_from.isoformat() if date_from else "",
        date_to=date_to.isoformat() if date_to else "",
        status=status,
        clients=clients_list,
        products=products_list,
    )


@app.route("/sales/add", methods=["POST"])
@login_required
def add_sale():
    user = current_user()

    date_str = request.form.get("date") or ""
    date_value = parse_date(date_str, date.today())

    client_id = request.form.get("client_id") or None
    client_name = request.form.get("client_name", "").strip()
    product_name = request.form.get("product", "").strip()

    cost_per_unit = float(request.form.get("cost_per_unit") or 0)
    price_per_unit = float(request.form.get("price_per_unit") or 0)
    quantity = int(request.form.get("quantity") or 1)

    payment_type = request.form.get("payment_type", "Contado")
    amount_paid = float(request.form.get("amount_paid") or 0)
    due_date = parse_date(request.form.get("due_date"), None)
    notes = request.form.get("notes", "").strip()

    total = price_per_unit * quantity
    profit = (price_per_unit - cost_per_unit) * quantity

    pending_amount = total - amount_paid
    status = "Pagado" if pending_amount <= 0 else "Pendiente"

    sale = Sale(
        date=date_value,
        status=status,
        name=client_name,
        product=product_name,
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
        client_id=int(client_id) if client_id else None,
        user_id=user.id,
    )

    db.session.add(sale)
    db.session.commit()
    return redirect(url_for("sales"))


@app.route("/sales/edit/<int:sale_id>", methods=["POST"])
@login_required
def edit_sale(sale_id):
    user = current_user()
    sale = Sale.query.filter_by(id=sale_id, user_id=user.id).first_or_404()

    date_str = request.form.get("date") or ""
    sale.date = parse_date(date_str, sale.date)

    sale.name = request.form.get("client_name", "").strip()
    sale.product = request.form.get("product", "").strip()
    sale.cost_per_unit = float(request.form.get("cost_per_unit") or 0)
    sale.price_per_unit = float(request.form.get("price_per_unit") or 0)
    sale.quantity = int(request.form.get("quantity") or 1)

    sale.payment_type = request.form.get("payment_type", "Contado")
    sale.amount_paid = float(request.form.get("amount_paid") or 0)
    sale.due_date = parse_date(request.form.get("due_date"), None)
    sale.notes = request.form.get("notes", "").strip()

    sale.total = sale.price_per_unit * sale.quantity
    sale.profit = (sale.price_per_unit - sale.cost_per_unit) * sale.quantity
    sale.pending_amount = sale.total - sale.amount_paid
    sale.status = "Pagado" if sale.pending_amount <= 0 else "Pendiente"

    db.session.commit()
    return redirect(url_for("sales"))


@app.route("/sales/delete/<int:sale_id>", methods=["POST"])
@login_required
def delete_sale(sale_id):
    user = current_user()
    sale = Sale.query.filter_by(id=sale_id, user_id=user.id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("sales"))


# -----------------------------------------------------------------------------
# Daily flow (flujo)
# -----------------------------------------------------------------------------


@app.route("/flow", methods=["GET", "POST"])
@login_required
def flow():
    user = current_user()

    if request.method == "POST":
        date_value = parse_date(request.form.get("date"), date.today())

        initial_custody = float(request.form.get("initial_custody") or 0)
        initial_capital = float(request.form.get("initial_capital") or 0)
        initial_external_accounts = float(
            request.form.get("initial_external_accounts") or 0
        )

        gross_profit = float(request.form.get("gross_profit") or 0)
        taxes_paid = float(request.form.get("taxes_paid") or 0)
        expenses_paid = float(request.form.get("expenses_paid") or 0)
        reinvestment = float(request.form.get("reinvestment") or 0)
        custody_used_for_fxd = float(
            request.form.get("custody_used_for_fxd") or 0
        )
        capital_for_fxd = float(request.form.get("capital_for_fxd") or 0)

        final_custody = float(request.form.get("final_custody") or 0)
        final_capital = float(request.form.get("final_capital") or 0)
        final_external_accounts = float(
            request.form.get("final_external_accounts") or 0
        )

        updated_capital = float(request.form.get("updated_capital") or 0)
        updated_custody = float(request.form.get("updated_custody") or 0)
        updated_external_accounts = float(
            request.form.get("updated_external_accounts") or 0
        )

        notes = request.form.get("notes", "").strip()

        record = DailyFlow.query.filter_by(
            user_id=user.id, date=date_value
        ).first()
        if not record:
            record = DailyFlow(user_id=user.id, date=date_value)

        record.initial_custody = initial_custody
        record.initial_capital = initial_capital
        record.initial_external_accounts = initial_external_accounts

        record.gross_profit = gross_profit
        record.taxes_paid = taxes_paid
        record.expenses_paid = expenses_paid
        record.reinvestment = reinvestment
        record.custody_used_for_fxd = custody_used_for_fxd
        record.capital_for_fxd = capital_for_fxd

        record.final_custody = final_custody
        record.final_capital = final_capital
        record.final_external_accounts = final_external_accounts

        record.updated_capital = updated_capital
        record.updated_custody = updated_custody
        record.updated_external_accounts = updated_external_accounts

        record.notes = notes

        db.session.add(record)
        db.session.commit()

        return redirect(url_for("flow"))

    # GET
    today_val = date.today()
    record = DailyFlow.query.filter_by(user_id=user.id, date=today_val).first()
    if not record:
        record = DailyFlow(user_id=user.id, date=today_val)

    expenses = (
        Expense.query.filter_by(user_id=user.id)
        .order_by(Expense.date.desc())
        .limit(20)
        .all()
    )

    total_expenses = sum(e.amount or 0 for e in expenses)
    total_reinvestments = sum(
        e.amount or 0 for e in expenses if e.category == "Reinversión"
    )
    total_other = sum(
        e.amount or 0
        for e in expenses
        if e.category not in ["Gasto", "Reinversión"]
    )

    return render_template(
        "flujo.html",
        record=record,
        expenses=expenses,
        total_expenses=total_expenses,
        total_reinvestments=total_reinvestments,
        total_other=total_other,
    )


@app.route("/expenses/add", methods=["POST"])
@login_required
def add_expense():
    user = current_user()
    date_value = parse_date(request.form.get("date"), date.today())
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip() or "Gasto"
    amount = float(request.form.get("amount") or 0)
    notes = request.form.get("notes", "").strip()

    e = Expense(
        user_id=user.id,
        date=date_value,
        description=description,
        category=category,
        amount=amount,
        notes=notes,
    )
    db.session.add(e)
    db.session.commit()
    return redirect(url_for("flow"))


@app.route("/expenses/delete/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    user = current_user()
    e = Expense.query.filter_by(id=expense_id, user_id=user.id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    return redirect(url_for("flow"))


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

    sales_q = Sale.query.filter_by(user_id=user.id).filter(
        Sale.date >= start, Sale.date <= end
    )
    sales_period = sales_q.all()

    total_monto_period = sum(s.total or 0 for s in sales_period)
    total_ganancia = sum(s.profit or 0 for s in sales_period)
    total_ventas_period = len(sales_period)

    days_diff = (end - start).days + 1
    avg_daily_profit = total_ganancia / days_diff if days_diff > 0 else 0.0
    avg_ticket = (
        total_monto_period / total_ventas_period
        if total_ventas_period > 0
        else 0.0
    )

    # Top productos por ganancia
    product_profit = {}
    for s in sales_period:
        if not s.product:
            continue
        product_profit.setdefault(s.product, 0.0)
        product_profit[s.product] += s.profit or 0.0

    sorted_products = sorted(
        product_profit.items(), key=lambda x: x[1], reverse=True
    )[:6]
    top_labels = [p[0] for p in sorted_products]
    top_values = [round(p[1], 2) for p in sorted_products]

    # Ganancia por semana
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

    # Máximo y mínimo de ganancia semanal
    if weekly_profit:
        max_weekly_profit = max(weekly_profit.values())
        min_weekly_profit = min(weekly_profit.values())
    else:
        max_weekly_profit = 0.0
        min_weekly_profit = 0.0

    # Ganancia por usuario (solo el actual)
    user_profit = {user.username: total_ganancia}
    user_labels = list(user_profit.keys())
    user_values = [round(v, 2) for v in user_profit.values()]

    today_dt = date.today()
    overdue_sales = (
        Sale.query.filter_by(user_id=user.id, status="Pendiente")
        .filter(Sale.due_date < today_dt)
        .all()
    )
    upcoming_sales = (
        Sale.query.filter_by(user_id=user.id, status="Pendiente")
        .filter(Sale.due_date >= today_dt)
        .all()
    )

    overdue_total = sum(s.pending_amount or 0 for s in overdue_sales)
    upcoming_total = sum(s.pending_amount or 0 for s in upcoming_sales)
    overdue_count = len(overdue_sales)
    upcoming_count = len(upcoming_sales)

    alerts = []
    if overdue_total > 0:
        alerts.append(
            {
                "level": "danger",
                "title": "Pagos vencidos",
                "message": f"Tienes {overdue_count} ventas con pagos vencidos por un total de ₡{format_num(overdue_total)}.",
            }
        )
    if upcoming_total > 0:
        alerts.append(
            {
                "level": "warning",
                "title": "Pagos próximos",
                "message": f"Hay {upcoming_count} ventas pendientes con pagos próximos por ₡{format_num(upcoming_total)}.",
            }
        )

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
        max_weekly_profit=max_weekly_profit,
        min_weekly_profit=min_weekly_profit,
    )


# -----------------------------------------------------------------------------
# API producto
# -----------------------------------------------------------------------------


@app.route("/api/product/<int:product_id>")
@login_required
def api_product(product_id):
    user = current_user()
    p = Product.query.filter_by(id=product_id, user_id=user.id).first_or_404()
    return jsonify(
        {
            "id": p.id,
            "name": p.name,
            "cost": round(p.cost or 0, 2),
            "price": round(p.price or 0, 2),
            "margin_percent": round(p.margin_percent or 0, 2),
        }
    )


# -----------------------------------------------------------------------------
# Exportar datos a Excel
# -----------------------------------------------------------------------------


@app.route("/export/sales")
@login_required
def export_sales():
    user = current_user()
    sales_list = (
        Sale.query.filter_by(user_id=user.id)
        .order_by(Sale.date.asc())
        .all()
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ventas"

    ws.append(
        [
            "Fecha",
            "Cliente",
            "Producto",
            "Cantidad",
            "Precio unitario",
            "Total",
            "Ganancia",
            "Estado",
            "Pendiente",
            "Tipo pago",
        ]
    )

    for s in sales_list:
        ws.append(
            [
                s.date.isoformat() if s.date else "",
                s.name or "",
                s.product or "",
                s.quantity or 0,
                s.price_per_unit or 0,
                s.total or 0,
                s.profit or 0,
                s.status or "",
                s.pending_amount or 0,
                s.payment_type or "",
            ]
        )

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="ventas.xlsx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
