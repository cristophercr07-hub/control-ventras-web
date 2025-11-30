import os
import datetime
import io
from collections import defaultdict

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
# CONFIGURACIÓN GENERAL (Multi-entorno)
# ---------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "clave-super-secreta")

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///ventas.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Margen mínimo eliminado → ahora margen ilimitado
MIN_MARGIN_PERCENT = 0.0


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    margin_percent = db.Column(db.Float, default=0.0)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)

    date = db.Column(db.Date, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    product = db.Column(db.String(120), nullable=False)

    status = db.Column(db.String(20), nullable=False)  # Pagado / Pendiente

    cost_per_unit = db.Column(db.Float, default=0.0)
    price_per_unit = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)

    total = db.Column(db.Float, default=0.0)
    profit = db.Column(db.Float, default=0.0)

    amount_paid = db.Column(db.Float, default=0.0)
    pending_amount = db.Column(db.Float, default=0.0)
    due_date = db.Column(db.Date)
    notes = db.Column(db.String(255))


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)

    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(20), nullable=False)  # Gasto / Reinversión
    amount = db.Column(db.Float, default=0.0)


# ---------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------

def parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except:
        return None


def current_user_id():
    return session.get("user_id")


def require_login():
    if not current_user_id():
        return redirect(url_for("login"))


# ---------------------------------------------------------
# CREACIÓN DE BD + ADMIN POR DEFECTO
# ---------------------------------------------------------

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()


# ---------------------------------------------------------
# LOGIN / LOGOUT
# ---------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user_id():
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["username"] = user.username
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
# USUARIOS (ADMIN)
# ---------------------------------------------------------

@app.route("/usuarios", methods=["GET", "POST"])
def usuarios():
    if not current_user_id():
        return redirect(url_for("login"))

    if not session.get("is_admin"):
        return redirect(url_for("dashboard"))

    error = None
    success = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            error = "Usuario y contraseña requeridos."
        else:
            exists = User.query.filter_by(username=username).first()
            if exists:
                error = "Ya existe ese usuario."
            else:
                user = User(
                    username=username,
                    password_hash=generate_password_hash(password),
                    is_admin=is_admin,
                )
                db.session.add(user)
                db.session.commit()
                success = "Usuario creado correctamente."

    users = User.query.order_by(User.username).all()

    return render_template("usuarios.html", users=users, error=error, success=success)


# ---------------------------------------------------------
# RUTA INICIAL
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
def productos():
    if not current_user_id():
        return redirect(url_for("login"))

    error = None
    success = None

    product_name_input = ""
    cost_input = ""
    margin_input = "0"
    quantity_input = "1"

    price_result = None
    profit_unit = None
    profit_total = None
    margin_used = None

    if request.method == "POST":
        form_type = request.form.get("form_type") or "calculator"

        # --------------------------
        # CALCULADORA
        # --------------------------
        if form_type == "calculator":
            try:
                product_name_input = request.form.get("product_name") or ""
                cost = float(request.form.get("cost") or 0)
                margin = float(request.form.get("margin") or 0)
                quantity = int(request.form.get("quantity") or 1)
                save_to_catalog = bool(request.form.get("save_to_catalog"))

                if cost <= 0:
                    raise ValueError("El costo debe ser mayor que cero.")
                if quantity <= 0:
                    raise ValueError("La cantidad debe ser mayor que cero.")

                price_result = cost * (1 + margin / 100)
                profit_unit = price_result - cost
                profit_total = profit_unit * quantity
                margin_used = (profit_unit / cost * 100) if cost > 0 else 0

                if save_to_catalog:
                    if not product_name_input:
                        raise ValueError("Debes ingresar nombre del producto.")

                    existing = Product.query.filter_by(
                        user_id=current_user_id(),
                        name=product_name_input
                    ).first()

                    if existing:
                        existing.cost = cost
                        existing.price = price_result
                        existing.margin_percent = margin_used
                    else:
                        p = Product(
                            user_id=current_user_id(),
                            name=product_name_input,
                            cost=cost,
                            price=price_result,
                            margin_percent=margin_used,
                        )
                        db.session.add(p)

                    db.session.commit()
                    success = "Producto guardado en catálogo."

            except Exception as e:
                error = f"Error: {e}"

        # --------------------------
        # FORMULARIO DIRECTO
        # --------------------------
        elif form_type == "catalog":
            try:
                name = (request.form.get("name") or "").strip()
                cost = float(request.form.get("cost") or 0)
                price = float(request.form.get("price") or 0)

                if not name:
                    raise ValueError("El nombre es obligatorio.")

                margin_percent = 0
                if cost > 0 and price > 0:
                    margin_percent = (price - cost) / cost * 100

                existing = Product.query.filter_by(
                    user_id=current_user_id(),
                    name=name
                ).first()

                if existing:
                    existing.cost = cost
                    existing.price = price
                    existing.margin_percent = margin_percent
                else:
                    p = Product(
                        user_id=current_user_id(),
                        name=name,
                        cost=cost,
                        price=price,
                        margin_percent=margin_percent,
                    )
                    db.session.add(p)

                db.session.commit()
                success = "Producto guardado."
            except Exception as e:
                error = f"Error: {e}"

    products = Product.query.filter_by(user_id=current_user_id()).order_by(Product.name).all()

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


@app.post("/productos/<int:product_id>/delete")
def delete_product(product_id):
    if not current_user_id():
        return redirect(url_for("login"))

    p = Product.query.get_or_404(product_id)

    if p.user_id != current_user_id() and not session.get("is_admin"):
        return redirect(url_for("productos"))

    db.session.delete(p)
    db.session.commit()
    return redirect(url_for("productos"))


# ---------------------------------------------------------
# VENTAS
# ---------------------------------------------------------

def apply_sales_filters(query, name_filter, status_filter, d_from, d_to):
    if name_filter:
        query = query.filter(Sale.name.ilike(f"%{name_filter}%"))
    if status_filter:
        query = query.filter(Sale.status == status_filter)
    if d_from:
        query = query.filter(Sale.date >= d_from)
    if d_to:
        query = query.filter(Sale.date <= d_to)
    return query


@app.route("/sales", methods=["GET", "POST"])
def sales_page():
    if not current_user_id():
        return redirect(url_for("login"))

    error = None
    success = request.args.get("success")

    if request.method == "POST":
        try:
            date = parse_date(request.form.get("date")) or datetime.date.today()

            name = (request.form.get("name") or "").strip()
            product_select = (request.form.get("product_select") or "").strip()
            product_input = (request.form.get("product") or "").strip()

            product = product_input or product_select

            status = request.form.get("status") or "Pagado"
            quantity = int(request.form.get("quantity") or 1)
            cost_per_unit = float(request.form.get("cost_per_unit") or 0)
            price_per_unit = float(request.form.get("price_per_unit") or 0)

            amount_paid = float(request.form.get("amount_paid") or 0)
            due_date = parse_date(request.form.get("due_date"))
            notes = request.form.get("notes") or ""

            if not name:
                raise ValueError("Debe ingresar un cliente.")
            if not product:
                raise ValueError("Debe elegir o escribir un producto.")
            if quantity <= 0:
                raise ValueError("Cantidad inválida.")

            total = price_per_unit * quantity
            profit = (price_per_unit - cost_per_unit) * quantity

            pending_amount = total - amount_paid if status == "Pendiente" else 0

            sale = Sale(
                user_id=current_user_id(),
                date=date,
                name=name,
                product=product,
                status=status,
                quantity=quantity,
                cost_per_unit=cost_per_unit,
                price_per_unit=price_per_unit,
                total=total,
                profit=profit,
                amount_paid=amount_paid,
                pending_amount=pending_amount,
                due_date=due_date,
                notes=notes,
            )
            db.session.add(sale)
            db.session.commit()
            success = "Venta guardada correctamente."

        except Exception as e:
            error = f"Error: {e}"

    # FILTROS
    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    sales_query = Sale.query.filter_by(user_id=current_user_id())
    sales_query = apply_sales_filters(sales_query, filter_name, filter_status, d_from, d_to)

    sales = sales_query.order_by(Sale.date.desc(), Sale.id.desc()).all()

    total_monto = sum(s.total for s in sales)
    total_ganancia = sum(s.profit for s in sales)
    total_pagado = sum(s.total for s in sales if s.status == "Pagado")
    total_pendiente = sum(s.pending_amount for s in sales if s.status == "Pendiente")

    products = Product.query.filter_by(user_id=current_user_id()).order_by(Product.name).all()

    return render_template(
        "ventas.html",
        error=error,
        success=success,
        sales=sales,
        products=products,

        filter_name=filter_name,
        filter_status=filter_status,
        date_from=date_from,
        date_to=date_to,

        total_monto=total_monto,
        total_ganancia=total_ganancia,
        total_pagado=total_pagado,
        total_pendiente=total_pendiente,
    )


@app.post("/sales/<int:sale_id>/delete")
def delete_sale(sale_id):
    if not current_user_id():
        return redirect(url_for("login"))

    s = Sale.query.get_or_404(sale_id)
    if s.user_id != current_user_id() and not session.get("is_admin"):
        return redirect(url_for("sales_page"))

    db.session.delete(s)
    db.session.commit()
    return redirect(url_for("sales_page", success="Venta eliminada correctamente."))


@app.route("/sales/export")
def sales_export():
    if not current_user_id():
        return redirect(url_for("login"))

    filter_name = request.args.get("filter_name") or ""
    filter_status = request.args.get("filter_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    query = Sale.query.filter_by(user_id=current_user_id())
    query = apply_sales_filters(query, filter_name, filter_status, d_from, d_to)

    sales = query.order_by(Sale.date.asc()).all()

    rows = []
    for s in sales:
        rows.append({
            "Fecha": s.date.isoformat(),
            "Cliente": s.name,
            "Producto": s.product,
            "Estado": s.status,
            "Cantidad": s.quantity,
            "Costo/U": s.cost_per_unit,
            "Precio/U": s.price_per_unit,
            "Total": s.total,
            "Ganancia": s.profit,
            "Pagado": s.amount_paid,
            "Pendiente": s.pending_amount,
            "Vence": s.due_date.isoformat() if s.due_date else "",
            "Notas": s.notes or "",
        })

    df = pd.DataFrame(rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Ventas")
    output.seek(0)

    return send_file(output,
                     as_attachment=True,
                     download_name="ventas.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------
# FLUJO DE CAJA
# ---------------------------------------------------------

@app.route("/flujo", methods=["GET", "POST"])
def flujo():
    if not current_user_id():
        return redirect(url_for("login"))

    error = None
    success = None

    # REGISTRO DE MOVIMIENTO
    if request.method == "POST":
        try:
            date = parse_date(request.form.get("date")) or datetime.date.today()
            description = request.form.get("description") or ""
            category = request.form.get("category") or "Gasto"
            amount = float(request.form.get("amount") or 0)

            if not description:
                raise ValueError("La descripción es obligatoria.")
            if amount <= 0:
                raise ValueError("El monto debe ser mayor a cero.")

            e = Expense(
                user_id=current_user_id(),
                date=date,
                description=description,
                category=category,
                amount=amount,
            )
            db.session.add(e)
            db.session.commit()
            success = "Movimiento registrado correctamente."

        except Exception as e:
            error = f"Error: {e}"

    # FILTROS
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    exp_query = Expense.query.filter_by(user_id=current_user_id())
    sales_query = Sale.query.filter_by(user_id=current_user_id())

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

    total_ingresos = sum(s.total for s in sales)
    total_ganancia = sum(s.profit for s in sales)

    total_gastos = sum(e.amount for e in expenses if e.category == "Gasto")
    total_reinv = sum(e.amount for e in expenses if e.category == "Reinversión")

    total_egresos = total_gastos + total_reinv
    neto = total_ganancia - total_egresos

    ahorro_objetivo = total_ganancia * 0.10
    ahorro_real = max(neto, 0)
    ahorro_faltante = max(ahorro_objetivo - ahorro_real, 0)
    meta_cumplida = ahorro_real >= ahorro_objetivo

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
def flujo_export():
    if not current_user_id():
        return redirect(url_for("login"))

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    category_filter = request.args.get("category_filter") or ""

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    q = Expense.query.filter_by(user_id=current_user_id())

    if d_from:
        q = q.filter(Expense.date >= d_from)
    if d_to:
        q = q.filter(Expense.date <= d_to)
    if category_filter:
        q = q.filter(Expense.category == category_filter)

    expenses = q.order_by(Expense.date.asc()).all()

    rows = [{
        "Fecha": e.date.isoformat(),
        "Descripción": e.description,
        "Tipo": e.category,
        "Monto (₡)": e.amount
    } for e in expenses]

    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Flujo")
    output.seek(0)

    return send_file(output,
                     as_attachment=True,
                     download_name="flujo.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    if not current_user_id():
        return redirect(url_for("login"))

    preset = request.args.get("preset") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    d_from = parse_date(date_from)
    d_to = parse_date(date_to)

    today = datetime.date.today()

    if preset:
        if preset == "week":
            d_to = today
            d_from = today - datetime.timedelta(days=7)
        elif preset == "4weeks":
            d_to = today
            d_from = today - datetime.timedelta(days=28)
        elif preset == "month":
            d_to = today
            d_from = today.replace(day=1)
        elif preset == "year":
            d_to = today
            d_from = today.replace(month=1, day=1)

        date_from = d_from.isoformat()
        date_to = d_to.isoformat()

    sales_query = Sale.query.filter_by(user_id=current_user_id())

    if d_from:
        sales_query = sales_query.filter(Sale.date >= d_from)
    if d_to:
        sales_query = sales_query.filter(Sale.date <= d_to)

    sales = sales_query.order_by(Sale.date).all()

    # TOP PRODUCTOS
    profit_by_product = defaultdict(float)
    for s in sales:
        profit_by_product[s.product] += s.profit

    top_items = sorted(profit_by_product.items(),
                       key=lambda x: x[1],
                       reverse=True)[:5]

    top_labels = [name for name, _ in top_items]
    top_values = [value for _, value in top_items]

    # GANANCIA POR SEMANA
    profit_by_week = defaultdict(float)
    for s in sales:
        if not s.date:
            continue
        y, w, _ = s.date.isocalendar()
        key = f"{y}-W{w:02d}"
        profit_by_week[key] += s.profit

    weeks_sorted = sorted(profit_by_week.items(),
                          key=lambda x: x[0])

    week_labels = [k for k, _ in weeks_sorted]
    week_values = [round(v, 2) for _, v in weeks_sorted]

    # TOTAL GANANCIA
    total_ganancia = sum(s.profit for s in sales)

    # PAGOS VENCIDOS Y PRÓXIMOS
    today = datetime.date.today()
    overdue_sales = [s for s in sales if s.status == "Pendiente" and s.due_date and s.due_date < today]
    upcoming_sales = [s for s in sales if s.status == "Pendiente" and s.due_date and s.due_date >= today]

    overdue_total = sum(s.pending_amount for s in overdue_sales)
    overdue_count = len(overdue_sales)

    upcoming_total = sum(s.pending_amount for s in upcoming_sales)
    upcoming_count = len(upcoming_sales)

    return render_template(
        "dashboard.html",
        top_labels=top_labels,
        top_values=top_values,
        week_labels=week_labels,
        week_values=week_values,
        total_ganancia=total_ganancia,

        overdue_total=overdue_total,
        overdue_count=overdue_count,
        upcoming_total=upcoming_total,
        upcoming_count=upcoming_count,

        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
