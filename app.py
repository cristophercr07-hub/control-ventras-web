import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from dataclasses import dataclass
from datetime import datetime, date
from collections import defaultdict
import csv
import os
import sys

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ================== CONFIGURACIÓN TEMA OSCURO ==================

BACKGROUND = "#060814"
BACKGROUND_ELEVATED = "#101426"
CARD = "#181c34"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#aaaaaa"
ACCENT = "#8c6eff"

TITLE_FONT = ("Arial Narrow", 14)
DATA_FONT = ("Arial Narrow", 12)

# Lista fija de productos
PRODUCTS = [
    "Flor Nacional",
    "Flor Gringa",
    "Miel",
    "Preroll",
    "Gomitas",
    "Snowballs",
    "Empanizador",
]

# Valores por defecto del usuario (primer uso)
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "1234"

# Carpeta base (soporta .py y .exe)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_FILE = os.path.join(BASE_DIR, "ventas_data.csv")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.csv")


def load_credentials():
    """
    Carga usuario y contraseña desde credentials.csv.
    Si no existe o está vacío, devuelve los valores por defecto.
    """
    if not os.path.exists(CREDENTIALS_FILE):
        return DEFAULT_USERNAME, DEFAULT_PASSWORD

    try:
        with open(CREDENTIALS_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader, None)
            if not row:
                return DEFAULT_USERNAME, DEFAULT_PASSWORD
            username = row.get("username", DEFAULT_USERNAME)
            password = row.get("password", DEFAULT_PASSWORD)
            return username, password
    except Exception:
        return DEFAULT_USERNAME, DEFAULT_PASSWORD


def save_credentials(username: str, password: str):
    """
    Guarda usuario y contraseña en credentials.csv.
    """
    fieldnames = ["username", "password"]
    with open(CREDENTIALS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"username": username, "password": password})


@dataclass
class Sale:
    date: date
    name: str
    product: str
    cost_per_unit: float
    price_per_unit: float
    quantity: int

    @property
    def total_cost(self) -> float:
        return self.cost_per_unit * self.quantity

    @property
    def total_price(self) -> float:
        return self.price_per_unit * self.quantity

    @property
    def profit(self) -> float:
        return self.total_price - self.total_cost

    @property
    def margin_percent(self) -> float:
        if self.total_cost == 0:
            return 0.0
        return (self.profit / self.total_cost) * 100.0


class SalesApp:
    def __init__(self, root: tk.Tk, username: str, password: str):
        self.root = root
        self.root.title("Control de Ventas - Windows")
        self.root.configure(bg=BACKGROUND)
        self.root.geometry("1100x650")

        # Credenciales actuales en uso
        self.username = username
        self.password = password

        self.sales: list[Sale] = []
        self.filtered_sales: list[Sale] = []

        # Filtro de fechas
        self.filter_start_var = tk.StringVar()
        self.filter_end_var = tk.StringVar()

        self._setup_style()
        self._build_ui()

        self.load_sales()
        self.apply_filter()

    # ========== ESTILO ==========

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TNotebook", background=BACKGROUND)
        style.configure(
            "TNotebook.Tab",
            background=BACKGROUND_ELEVATED,
            foreground=TEXT_SECONDARY,
            padding=(10, 5),
            font=TITLE_FONT,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", CARD)],
            foreground=[("selected", TEXT_PRIMARY)],
        )

        style.configure("Dark.TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background=CARD)

        style.configure(
            "Dark.TLabel",
            background=BACKGROUND,
            foreground=TEXT_PRIMARY,
            font=TITLE_FONT,
        )
        style.configure(
            "Card.TLabel",
            background=CARD,
            foreground=TEXT_PRIMARY,
            font=TITLE_FONT,
        )
        style.configure(
            "Secondary.TLabel",
            background=BACKGROUND,
            foreground=TEXT_SECONDARY,
            font=DATA_FONT,
        )

        style.configure(
            "Dark.TButton",
            background=CARD,
            foreground=TEXT_PRIMARY,
            padding=6,
            font=DATA_FONT,
        )
        style.map(
            "Dark.TButton",
            background=[("active", ACCENT)],
            foreground=[("active", "#ffffff")],
        )

        style.configure(
            "Treeview",
            background=CARD,
            foreground=TEXT_PRIMARY,
            fieldbackground=CARD,
            rowheight=24,
            font=DATA_FONT,
        )
        style.configure(
            "Treeview.Heading",
            background=BACKGROUND_ELEVATED,
            foreground=TEXT_PRIMARY,
            font=TITLE_FONT,
        )
        style.map(
            "Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#ffffff")],
        )

    # ========== UI GENERAL ==========

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.sales_frame = ttk.Frame(notebook, style="Dark.TFrame")
        self.dashboard_frame = ttk.Frame(notebook, style="Dark.TFrame")

        notebook.add(self.sales_frame, text="Ventas")
        notebook.add(self.dashboard_frame, text="Dashboard")

        self._build_sales_tab()
        self._build_dashboard_tab()

    # ========== TAB VENTAS ==========

    def _build_sales_tab(self):
        # Barra superior izquierda con botón de configuración de usuario
        header_bar = ttk.Frame(self.sales_frame, style="Dark.TFrame")
        header_bar.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Button(
            header_bar,
            text="Configurar usuario",
            style="Dark.TButton",
            command=self.configure_user,
        ).pack(side="left")

        # --- Formulario arriba ---
        form_card = ttk.Frame(self.sales_frame, style="Card.TFrame")
        form_card.pack(fill="x", padx=8, pady=8)

        self.name_var = tk.StringVar()
        self.product_var = tk.StringVar()
        self.cost_var = tk.StringVar()
        self.price_var = tk.StringVar()
        self.qty_var = tk.StringVar(value="1")
        self.date_var = tk.StringVar(value=date.today().strftime("%Y-%m-%d"))

        def make_label(frame, txt):
            return ttk.Label(frame, text=txt, style="Card.TLabel", anchor="center")

        # Primera fila
        row1 = ttk.Frame(form_card, style="Card.TFrame")
        row1.pack(fill="x", padx=8, pady=4)

        make_label(row1, "Nombre / Cliente:").grid(row=0, column=0, sticky="we")
        entry_name = ttk.Entry(
            row1, textvariable=self.name_var, width=25, justify="center"
        )
        entry_name.grid(row=1, column=0, padx=(0, 10))

        make_label(row1, "Producto:").grid(row=0, column=1, sticky="we")
        self.product_combo = ttk.Combobox(
            row1,
            textvariable=self.product_var,
            values=PRODUCTS,
            state="readonly",
            width=23,
            justify="center",
        )
        self.product_combo.grid(row=1, column=1, padx=(0, 10))
        if PRODUCTS:
            self.product_combo.current(0)

        make_label(row1, "Fecha (YYYY-MM-DD):").grid(row=0, column=2, sticky="we")
        entry_date = ttk.Entry(
            row1, textvariable=self.date_var, width=15, justify="center"
        )
        entry_date.grid(row=1, column=2, padx=(0, 10))

        # Segunda fila
        row2 = ttk.Frame(form_card, style="Card.TFrame")
        row2.pack(fill="x", padx=8, pady=4)

        make_label(row2, "Costo por unidad:").grid(row=0, column=0, sticky="we")
        entry_cost = ttk.Entry(
            row2, textvariable=self.cost_var, width=15, justify="right"
        )
        entry_cost.grid(row=1, column=0, padx=(0, 10))

        make_label(row2, "Precio por unidad:").grid(row=0, column=1, sticky="we")
        entry_price = ttk.Entry(
            row2, textvariable=self.price_var, width=15, justify="right"
        )
        entry_price.grid(row=1, column=1, padx=(0, 10))

        make_label(row2, "Cantidad:").grid(row=0, column=2, sticky="we")
        entry_qty = ttk.Entry(
            row2, textvariable=self.qty_var, width=8, justify="right"
        )
        entry_qty.grid(row=1, column=2, padx=(0, 10))

        btn_row = ttk.Frame(form_card, style="Card.TFrame")
        btn_row.pack(fill="x", padx=8, pady=4)

        ttk.Button(
            btn_row,
            text="Agregar venta",
            style="Dark.TButton",
            command=self.add_sale,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            btn_row,
            text="Eliminar seleccionada",
            style="Dark.TButton",
            command=self.delete_selected,
        ).pack(side="left")

        # --- Tabla central ---
        table_frame = ttk.Frame(self.sales_frame, style="Dark.TFrame")
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = (
            "date",
            "name",
            "product",
            "cost",
            "price",
            "qty",
            "total",
            "profit",
            "margin",
        )
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            style="Treeview",
        )
        headings = {
            "date": "Fecha",
            "name": "Nombre",
            "product": "Producto",
            "cost": "Costo u.",
            "price": "Precio u.",
            "qty": "Cant.",
            "total": "Total venta",
            "profit": "Ganancia",
            "margin": "Utilidad %",
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])

        # Alineación: textos centrados, números a la derecha
        self.tree.column("date", width=90, anchor="center")
        self.tree.column("name", width=130, anchor="center")
        self.tree.column("product", width=130, anchor="center")
        self.tree.column("cost", width=80, anchor="e")
        self.tree.column("price", width=80, anchor="e")
        self.tree.column("qty", width=60, anchor="e")
        self.tree.column("total", width=100, anchor="e")
        self.tree.column("profit", width=100, anchor="e")
        self.tree.column("margin", width=80, anchor="e")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # --- Filtro y resumen abajo ---
        bottom = ttk.Frame(self.sales_frame, style="Dark.TFrame")
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        filter_frame = ttk.Frame(bottom, style="Dark.TFrame")
        filter_frame.pack(side="left", padx=4)

        ttk.Label(
            filter_frame,
            text="Desde (YYYY-MM-DD):",
            style="Secondary.TLabel",
            anchor="center",
        ).grid(row=0, column=0, sticky="we")
        ttk.Entry(
            filter_frame,
            textvariable=self.filter_start_var,
            width=12,
            justify="right",
        ).grid(row=1, column=0, padx=(0, 8))

        ttk.Label(
            filter_frame,
            text="Hasta:",
            style="Secondary.TLabel",
            anchor="center",
        ).grid(row=0, column=1, sticky="we")
        ttk.Entry(
            filter_frame,
            textvariable=self.filter_end_var,
            width=12,
            justify="right",
        ).grid(row=1, column=1, padx=(0, 8))

        ttk.Button(
            filter_frame,
            text="Aplicar filtro",
            style="Dark.TButton",
            command=self.apply_filter,
        ).grid(row=1, column=2, padx=(0, 8))

        ttk.Button(
            filter_frame,
            text="Quitar filtro",
            style="Dark.TButton",
            command=self.clear_filter,
        ).grid(row=1, column=3)

        summary_frame = ttk.Frame(bottom, style="Dark.TFrame")
        summary_frame.pack(side="right", padx=4)

        self.summary_label = ttk.Label(
            summary_frame,
            text="Ganancia total mostrada: ₡0.00",
            style="Dark.TLabel",
            anchor="center",
        )
        self.summary_label.pack(anchor="e")

    # ========== TAB DASHBOARD ==========

    def _build_dashboard_tab(self):
        top_frame = ttk.Frame(self.dashboard_frame, style="Dark.TFrame")
        top_frame.pack(fill="x", padx=8, pady=8)

        self.lbl_total_revenue = ttk.Label(
            top_frame, text="Ingresos: ₡0.00", style="Dark.TLabel", anchor="center"
        )
        self.lbl_total_cost = ttk.Label(
            top_frame, text="Costo: ₡0.00", style="Dark.TLabel", anchor="center"
        )
        self.lbl_total_profit = ttk.Label(
            top_frame, text="Ganancia: ₡0.00", style="Dark.TLabel", anchor="center"
        )
        self.lbl_margin = ttk.Label(
            top_frame, text="Margen: 0.0%", style="Dark.TLabel", anchor="center"
        )
        self.lbl_units = ttk.Label(
            top_frame, text="Unidades: 0", style="Dark.TLabel", anchor="center"
        )
        self.lbl_orders = ttk.Label(
            top_frame, text="Órdenes: 0", style="Dark.TLabel", anchor="center"
        )

        self.lbl_total_revenue.grid(row=0, column=0, sticky="we", padx=4, pady=2)
        self.lbl_total_cost.grid(row=1, column=0, sticky="we", padx=4, pady=2)
        self.lbl_total_profit.grid(row=2, column=0, sticky="we", padx=4, pady=2)
        self.lbl_margin.grid(row=0, column=1, sticky="we", padx=4, pady=2)
        self.lbl_units.grid(row=1, column=1, sticky="we", padx=4, pady=2)
        self.lbl_orders.grid(row=2, column=1, sticky="we", padx=4, pady=2)

        chart_card = ttk.Frame(self.dashboard_frame, style="Card.TFrame")
        chart_card.pack(fill="both", expand=True, padx=8, pady=8)

        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.ax_top_products = self.fig.add_subplot(211)
        self.ax_daily_profit = self.fig.add_subplot(212)

        self.ax_top_products.set_facecolor("#141827")
        self.ax_daily_profit.set_facecolor("#141827")
        self.fig.patch.set_facecolor(CARD)

        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_card)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # ========== LÓGICA VENTAS ==========

    def parse_date(self, s: str | None) -> date | None:
        if not s:
            return None
        try:
            return datetime.strptime(s.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    def add_sale(self):
        name = self.name_var.get().strip()
        product = self.product_var.get().strip()
        cost_str = self.cost_var.get().replace(",", ".").strip()
        price_str = self.price_var.get().replace(",", ".").strip()
        qty_str = self.qty_var.get().strip()
        date_str = self.date_var.get().strip()

        if not name or not product:
            messagebox.showerror("Error", "Nombre y producto son obligatorios.")
            return

        d = self.parse_date(date_str)
        if d is None:
            messagebox.showerror("Error", "Fecha inválida. Usa formato YYYY-MM-DD.")
            return

        try:
            cost = float(cost_str)
            price = float(price_str)
            qty = int(qty_str)
        except ValueError:
            messagebox.showerror(
                "Error", "Costo, precio y cantidad deben ser números."
            )
            return

        if qty <= 0:
            messagebox.showerror("Error", "La cantidad debe ser mayor que cero.")
            return

        sale = Sale(
            date=d,
            name=name,
            product=product,
            cost_per_unit=cost,
            price_per_unit=price,
            quantity=qty,
        )
        self.sales.append(sale)
        self.save_sales()
        self.apply_filter()

        self.product_var.set(PRODUCTS[0] if PRODUCTS else "")
        self.cost_var.set("")
        self.price_var.set("")
        self.qty_var.set("1")

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Eliminar", "Selecciona una fila para eliminar.")
            return

        if not messagebox.askyesno(
            "Confirmar", "¿Eliminar la(s) venta(s) seleccionada(s)?"
        ):
            return

        for item_id in selected:
            index_in_filtered = int(self.tree.item(item_id, "text"))
            sale_obj = self.filtered_sales[index_in_filtered]
            if sale_obj in self.sales:
                self.sales.remove(sale_obj)

        self.save_sales()
        self.apply_filter()

    def load_sales(self):
        if not os.path.exists(DATA_FILE):
            return
        self.sales.clear()
        with open(DATA_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = self.parse_date(row.get("date", ""))
                if d is None:
                    continue
                try:
                    sale = Sale(
                        date=d,
                        name=row.get("name", ""),
                        product=row.get("product", ""),
                        cost_per_unit=float(row.get("cost_per_unit", "0")),
                        price_per_unit=float(row.get("price_per_unit", "0")),
                        quantity=int(row.get("quantity", "0")),
                    )
                    self.sales.append(sale)
                except ValueError:
                    continue

    def save_sales(self):
        fieldnames = [
            "date",
            "name",
            "product",
            "cost_per_unit",
            "price_per_unit",
            "quantity",
        ]
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for s in self.sales:
                writer.writerow(
                    {
                        "date": s.date.strftime("%Y-%m-%d"),
                        "name": s.name,
                        "product": s.product,
                        "cost_per_unit": f"{s.cost_per_unit:.4f}",
                        "price_per_unit": f"{s.price_per_unit:.4f}",
                        "quantity": s.quantity,
                    }
                )

    def clear_filter(self):
        self.filter_start_var.set("")
        self.filter_end_var.set("")
        self.apply_filter()

    def apply_filter(self):
        start = self.parse_date(self.filter_start_var.get())
        end = self.parse_date(self.filter_end_var.get())

        if start and end and start > end:
            start, end = end, start

        self.filtered_sales = []
        for s in self.sales:
            if start and s.date < start:
                continue
            if end and s.date > end:
                continue
            self.filtered_sales.append(s)

        self.refresh_table()
        self.refresh_summary()
        self.refresh_dashboard()

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for idx, s in enumerate(self.filtered_sales):
            self.tree.insert(
                "",
                "end",
                text=str(idx),
                values=(
                    s.date.strftime("%Y-%m-%d"),
                    s.name,
                    s.product,
                    f"{s.cost_per_unit:,.2f}",
                    f"{s.price_per_unit:,.2f}",
                    s.quantity,
                    f"{s.total_price:,.2f}",
                    f"{s.profit:,.2f}",
                    f"{s.margin_percent:.1f}",
                ),
            )

    def refresh_summary(self):
        total_profit = sum(s.profit for s in self.filtered_sales)
        self.summary_label.config(
            text=f"Ganancia total mostrada: ₡{total_profit:,.2f}"
        )

    # ========== DASHBOARD (GRÁFICOS PASTEL) ==========

    def refresh_dashboard(self):
        sales = self.filtered_sales

        total_revenue = sum(s.total_price for s in sales)
        total_cost = sum(s.total_cost for s in sales)
        total_profit = sum(s.profit for s in sales)
        total_units = sum(s.quantity for s in sales)
        total_orders = len(sales)
        margin = (total_profit / total_cost * 100) if total_cost > 0 else 0.0

        self.lbl_total_revenue.config(text=f"Ingresos: ₡{total_revenue:,.2f}")
        self.lbl_total_cost.config(text=f"Costo: ₡{total_cost:,.2f}")
        self.lbl_total_profit.config(text=f"Ganancia: ₡{total_profit:,.2f}")
        self.lbl_margin.config(text=f"Margen: {margin:.1f}%")
        self.lbl_units.config(text=f"Unidades: {total_units}")
        self.lbl_orders.config(text=f"Órdenes: {total_orders}")

        # --- Gráfico pastel: top productos por ganancia ---
        self.ax_top_products.clear()
        self.ax_top_products.set_facecolor("#141827")

        if sales:
            profit_by_product = defaultdict(float)
            for s in sales:
                profit_by_product[s.product] += s.profit

            items = sorted(
                profit_by_product.items(), key=lambda x: x[1], reverse=True
            )[:5]
            labels = [i[0] for i in items]
            sizes = [i[1] for i in items]

            if sum(sizes) > 0:
                wedges, texts, autotexts = self.ax_top_products.pie(
                    sizes,
                    labels=labels,
                    autopct="%1.1f%%",
                    textprops={"color": "white", "fontsize": 8},
                )
                for t in texts:
                    t.set_color("white")
                self.ax_top_products.set_title(
                    "Top productos por ganancia", color="white"
                )
                self.ax_top_products.axis("equal")
            else:
                self.ax_top_products.set_title(
                    "Sin datos de productos (ganancia 0)", color="white"
                )
        else:
            self.ax_top_products.set_title("Sin datos de productos", color="white")

        # --- Gráfico pastel: distribución de ganancia por fecha ---
        self.ax_daily_profit.clear()
        self.ax_daily_profit.set_facecolor("#141827")

        if sales:
            profit_by_date = defaultdict(float)
            for s in sales:
                profit_by_date[s.date] += s.profit

            dates_sorted = sorted(profit_by_date.keys())
            labels = [d.strftime("%Y-%m-%d") for d in dates_sorted]
            sizes = [profit_by_date[d] for d in dates_sorted]

            if sum(sizes) > 0:
                wedges, texts, autotexts = self.ax_daily_profit.pie(
                    sizes,
                    labels=labels,
                    autopct="%1.1f%%",
                    textprops={"color": "white", "fontsize": 8},
                )
                for t in texts:
                    t.set_color("white")
                self.ax_daily_profit.set_title(
                    "Distribución de ganancia por día", color="white"
                )
                self.ax_daily_profit.axis("equal")
            else:
                self.ax_daily_profit.set_title(
                    "Sin datos de fechas (ganancia 0)", color="white"
                )
        else:
            self.ax_daily_profit.set_title("Sin datos de fechas", color="white")

        self.fig.tight_layout()
        self.canvas.draw()

    # ========== CONFIGURAR USUARIO ==========

    def configure_user(self):
        """
        Permite cambiar usuario y contraseña.
        Solo se aplica si se conoce la contraseña actual.
        """
        # Pide la contraseña actual como verificación
        current_pwd = simpledialog.askstring(
            "Verificación",
            "Ingrese la contraseña actual:",
            show="*",
            parent=self.root,
        )
        if current_pwd is None:
            return  # Cancelado
        if current_pwd != self.password:
            messagebox.showerror("Error", "Contraseña actual incorrecta.")
            return

        # Pide nuevo usuario
        new_user = simpledialog.askstring(
            "Nuevo usuario",
            "Ingrese el nuevo usuario:",
            parent=self.root,
        )
        if new_user is None or not new_user.strip():
            messagebox.showinfo("Info", "Usuario no modificado.")
            return
        new_user = new_user.strip()

        # Pide nueva contraseña
        new_pwd = simpledialog.askstring(
            "Nueva contraseña",
            "Ingrese la nueva contraseña:",
            show="*",
            parent=self.root,
        )
        if new_pwd is None or not new_pwd.strip():
            messagebox.showinfo("Info", "Contraseña no modificada.")
            return
        new_pwd = new_pwd.strip()

        # Guarda en archivo y actualiza en memoria
        save_credentials(new_user, new_pwd)
        self.username = new_user
        self.password = new_pwd

        messagebox.showinfo(
            "Éxito",
            "Usuario y contraseña actualizados.\n"
            "Se usarán en el próximo inicio de sesión.",
        )


def main():
    # 1) Cargar credenciales actuales (o defaults)
    stored_username, stored_password = load_credentials()

    root = tk.Tk()
    root.withdraw()  # ocultar ventana principal mientras se hace login

    # 2) Diálogo de login (máx 3 intentos)
    for attempt in range(3):
        user = simpledialog.askstring(
            "Acceso",
            "Usuario:",
            parent=root,
        )
        if user is None:
            root.destroy()
            return

        pwd = simpledialog.askstring(
            "Acceso",
            "Contraseña:",
            show="*",
            parent=root,
        )
        if pwd is None:
            root.destroy()
            return

        if user == stored_username and pwd == stored_password:
            break
        else:
            messagebox.showerror("Error", "Usuario o contraseña incorrectos.")

    else:
        messagebox.showerror("Bloqueado", "Demasiados intentos fallidos.")
        root.destroy()
        return

    # 3) Mostrar ventana principal
    root.deiconify()
    app = SalesApp(root, stored_username, stored_password)
    root.mainloop()


if __name__ == "__main__":
    main()
