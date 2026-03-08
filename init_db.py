import streamlit as st
import sqlite3
from datetime import datetime, date

DB = "loans.db"


def get_conn():
    return sqlite3.connect(DB, check_same_thread=False)


def setup_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS borrowers (
        borrower_id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL UNIQUE,
        phone TEXT,
        address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS loans (
        loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
        borrower_id INTEGER NOT NULL,
        principal REAL NOT NULL,
        term_months INTEGER NOT NULL,
        monthly_interest_rate REAL NOT NULL DEFAULT 0.15,
        total_due REAL NOT NULL,
        start_date TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (borrower_id) REFERENCES borrowers (borrower_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER NOT NULL,
        due_date TEXT,
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Unpaid',
        paid_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (loan_id) REFERENCES loans (loan_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_ledger (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        amount REAL NOT NULL,
        reference TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Add paid_at column if old DB exists without it
    cur.execute("PRAGMA table_info(payments)")
    cols = [row[1] for row in cur.fetchall()]
    if "paid_at" not in cols:
        cur.execute("ALTER TABLE payments ADD COLUMN paid_at TEXT")

    conn.commit()
    conn.close()


def add_borrower_if_missing(name):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT borrower_id FROM borrowers WHERE full_name = ?", (name,))
    row = cur.fetchone()

    if row:
        borrower_id = row[0]
    else:
        cur.execute("INSERT INTO borrowers (full_name) VALUES (?)", (name,))
        conn.commit()
        borrower_id = cur.lastrowid

    conn.close()
    return borrower_id


def add_cash_entry(entry_date, entry_type, amount, reference):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cash_ledger (entry_date, entry_type, amount, reference)
        VALUES (?, ?, ?, ?)
    """, (entry_date, entry_type, amount, reference))
    conn.commit()
    conn.close()


def cash_entry_exists(entry_type, reference):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT entry_id
        FROM cash_ledger
        WHERE entry_type = ? AND reference = ?
        LIMIT 1
    """, (entry_type, reference))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_cash_on_hand():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM cash_ledger")
    total = cur.fetchone()[0]
    conn.close()
    return total


def generate_payment_schedule(principal, term_months, start_date_str):
    total_due = round(principal + (principal * 0.15 * term_months), 2)
    installments = term_months * 2

    base_amount = round(total_due / installments, 2)
    amounts = [base_amount] * installments
    diff = round(total_due - sum(amounts), 2)
    amounts[-1] = round(amounts[-1] + diff, 2)

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    due_dates = []
    current = start_date

    while len(due_dates) < installments:
        year = current.year
        month = current.month

        d15 = date(year, month, 15)
        if d15 > start_date and len(due_dates) < installments:
            due_dates.append(d15)

        if month == 2:
            dend = date(year, month, 28)
        else:
            dend = date(year, month, 30)

        if dend > start_date and len(due_dates) < installments:
            due_dates.append(dend)

        if month == 12:
            current = date(year + 1, 1, 1)
        else:
            current = date(year, month + 1, 1)

    schedule = []
    for i in range(installments):
        schedule.append((due_dates[i].strftime("%Y-%m-%d"), amounts[i], "Unpaid"))

    return total_due, schedule


def add_loan_if_missing(name, principal, start_date, term_months, total_due):
    borrower_id = add_borrower_if_missing(name)
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT loan_id
        FROM loans
        WHERE borrower_id = ? AND principal = ? AND start_date = ? AND term_months = ?
    """, (borrower_id, principal, start_date, term_months))
    row = cur.fetchone()

    if row:
        loan_id = row[0]
    else:
        cur.execute("""
            INSERT INTO loans (
                borrower_id,
                principal,
                term_months,
                monthly_interest_rate,
                total_due,
                start_date,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (borrower_id, principal, term_months, 0.15, total_due, start_date, "active"))
        conn.commit()
        loan_id = cur.lastrowid

    conn.close()
    return loan_id


def add_payment_if_missing(loan_id, due_date, amount, status, paid_at=None):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT payment_id
        FROM payments
        WHERE loan_id = ? AND due_date = ? AND amount = ?
    """, (loan_id, due_date, amount))
    row = cur.fetchone()

    if not row:
        cur.execute("""
            INSERT INTO payments (loan_id, due_date, amount, status, paid_at)
            VALUES (?, ?, ?, ?, ?)
        """, (loan_id, due_date, amount, status, paid_at))
        conn.commit()

    conn.close()


def ensure_schedule_for_loan(loan_id, principal, term_months, start_date):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM payments WHERE loan_id = ?", (loan_id,))
    count = cur.fetchone()[0]

    if count == 0:
        _, schedule = generate_payment_schedule(principal, term_months, start_date)
        for due_date, amount, status in schedule:
            cur.execute("""
                INSERT INTO payments (loan_id, due_date, amount, status, paid_at)
                VALUES (?, ?, ?, ?, ?)
            """, (loan_id, due_date, amount, status, None))
        conn.commit()

    conn.close()


def ensure_all_loans_have_schedule():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT loan_id, principal, term_months, start_date
        FROM loans
    """)
    rows = cur.fetchall()
    conn.close()

    for loan_id, principal, term_months, start_date in rows:
        ensure_schedule_for_loan(loan_id, principal, term_months, start_date)


def create_manual_loan(name, principal, start_date, term_months):
    total_due, _ = generate_payment_schedule(principal, term_months, start_date)
    loan_id = add_loan_if_missing(name, principal, start_date, term_months, total_due)

    ensure_schedule_for_loan(loan_id, principal, term_months, start_date)

    ref = f"Loan {loan_id} - {name}"
    if not cash_entry_exists("DISBURSEMENT", ref):
        add_cash_entry(start_date, "DISBURSEMENT", -principal, ref)

    return loan_id


def seed_current_data():
    loans = [
        {
            "name": "Wychelle",
            "principal": 5000,
            "start_date": "2026-01-03",
            "term_months": 3,
            "total_due": 7250,
            "payments": [
                ("2026-01-15", 1210, "Paid"),
                ("2026-01-30", 1210, "Paid"),
                ("2026-02-15", 1210, "Paid"),
                ("2026-02-28", 1210, "Paid"),
                ("2026-03-15", 1210, "Unpaid"),
                ("2026-03-30", 1200, "Unpaid"),
            ]
        },
        {
            "name": "Tin",
            "principal": 5000,
            "start_date": "2026-01-03",
            "term_months": 3,
            "total_due": 7250,
            "payments": [
                ("2026-01-15", 1210, "Paid"),
                ("2026-01-30", 1210, "Paid"),
                ("2026-02-15", 1210, "Paid"),
                ("2026-02-28", 1210, "Paid"),
                ("2026-03-15", 1210, "Unpaid"),
                ("2026-03-30", 1200, "Unpaid"),
            ]
        },
        {
            "name": "Chris",
            "principal": 5000,
            "start_date": "2026-01-05",
            "term_months": 2,
            "total_due": 6500,
            "payments": [
                ("2026-02-05", 3250, "Paid"),
                ("2026-03-05", 3250, "Paid"),
            ]
        },
        {
            "name": "CJ",
            "principal": 5000,
            "start_date": "2026-01-16",
            "term_months": 3,
            "total_due": 7250,
            "payments": [
                ("2026-01-30", 1210, "Paid"),
                ("2026-02-15", 1210, "Paid"),
                ("2026-02-28", 1210, "Paid"),
                ("2026-03-15", 1210, "Unpaid"),
                ("2026-03-30", 1210, "Unpaid"),
                ("2026-04-15", 1200, "Unpaid"),
            ]
        },
        {
            "name": "Dan",
            "principal": 5000,
            "start_date": "2026-02-07",
            "term_months": 3,
            "total_due": 7250,
            "payments": [
                ("2026-02-28", 1210, "Paid"),
                ("2026-03-15", 1210, "Unpaid"),
                ("2026-03-30", 1210, "Unpaid"),
                ("2026-04-15", 1210, "Unpaid"),
                ("2026-04-30", 1210, "Unpaid"),
                ("2026-05-15", 1200, "Unpaid"),
            ]
        },
        {
            "name": "Homer",
            "principal": 3000,
            "start_date": "2026-02-19",
            "term_months": 2,
            "total_due": 3900,
            "payments": [
                ("2026-02-28", 975, "Paid"),
                ("2026-03-15", 975, "Unpaid"),
                ("2026-03-30", 975, "Unpaid"),
                ("2026-04-15", 975, "Unpaid"),
            ]
        },
        {
            "name": "Ramon",
            "principal": 3000,
            "start_date": "2026-03-05",
            "term_months": 3,
            "total_due": 4350,
            "payments": [
                ("2026-03-15", 725, "Unpaid"),
                ("2026-03-30", 725, "Unpaid"),
                ("2026-04-15", 725, "Unpaid"),
                ("2026-04-30", 725, "Unpaid"),
                ("2026-05-15", 725, "Unpaid"),
                ("2026-05-30", 725, "Unpaid"),
            ]
        },
        {
            "name": "RV",
            "principal": 1000,
            "start_date": "2026-03-01",
            "term_months": 1,
            "total_due": 1150,
            "payments": []
        },
        {
            "name": "Cza",
            "principal": 3600,
            "start_date": "2026-02-01",
            "term_months": 3,
            "total_due": 5220,
            "payments": []
        },
    ]

    for loan in loans:
        loan_id = add_loan_if_missing(
            loan["name"],
            loan["principal"],
            loan["start_date"],
            loan["term_months"],
            loan["total_due"]
        )

        disb_ref = f"Loan {loan_id} - {loan['name']}"
        if not cash_entry_exists("DISBURSEMENT", disb_ref):
            add_cash_entry(loan["start_date"], "DISBURSEMENT", -loan["principal"], disb_ref)

        if loan["payments"]:
            for due_date, amount, status in loan["payments"]:
                paid_at = due_date if status == "Paid" else None
                add_payment_if_missing(loan_id, due_date, amount, status, paid_at)

                if status == "Paid":
                    ref = f"Payment {loan_id} {due_date} {amount}"
                    if not cash_entry_exists("COLLECTION", ref):
                        add_cash_entry(due_date, "COLLECTION", amount, ref)
        else:
            ensure_schedule_for_loan(loan_id, loan["principal"], loan["term_months"], loan["start_date"])


def fetch_summary():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COALESCE(SUM(principal), 0) FROM loans")
    total_principal = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(total_due), 0) FROM loans")
    total_due = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'Paid'")
    total_collected = cur.fetchone()[0]

    outstanding = total_due - total_collected
    conn.close()
    return total_principal, total_due, total_collected, outstanding


def fetch_upcoming_collections():
    conn = get_conn()
    cur = conn.cursor()

    today_str = date.today().strftime("%Y-%m-%d")

    cur.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM payments
        WHERE status != 'Paid' AND due_date = ?
    """, (today_str,))
    due_today = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM payments
        WHERE status != 'Paid'
          AND due_date >= date('now')
          AND due_date <= date('now', '+7 day')
    """)
    due_7_days = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM payments
        WHERE status != 'Paid'
          AND due_date >= date('now')
          AND due_date <= date('now', '+30 day')
    """)
    due_30_days = cur.fetchone()[0]

    cur.execute("""
        SELECT p.due_date, b.full_name, p.amount
        FROM payments p
        JOIN loans l ON p.loan_id = l.loan_id
        JOIN borrowers b ON l.borrower_id = b.borrower_id
        WHERE p.status != 'Paid' AND p.due_date >= date('now')
        ORDER BY p.due_date, b.full_name
        LIMIT 20
    """)
    rows = cur.fetchall()

    conn.close()
    return due_today, due_7_days, due_30_days, rows


def fetch_loans():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            l.loan_id,
            b.full_name,
            l.principal,
            l.term_months,
            l.total_due,
            l.start_date,
            COALESCE(SUM(CASE WHEN p.status='Paid' THEN p.amount ELSE 0 END), 0) AS paid_so_far
        FROM loans l
        JOIN borrowers b ON b.borrower_id = l.borrower_id
        LEFT JOIN payments p ON p.loan_id = l.loan_id
        GROUP BY l.loan_id, b.full_name, l.principal, l.term_months, l.total_due, l.start_date
        ORDER BY l.start_date, b.full_name
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_payments_for_loan(loan_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT payment_id, due_date, amount, status, paid_at
        FROM payments
        WHERE loan_id = ?
        ORDER BY due_date
    """, (loan_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


setup_db()
ensure_all_loans_have_schedule()

st.title("Kelsey Lending")
st.caption("Loan Management System")

menu = st.sidebar.selectbox(
    "Menu",
    [
        "Dashboard",
        "Load Current Data",
        "Add Borrower / Loan",
        "View Loans",
        "Post Payment",
        "Cash Ledger"
    ]
)

if menu == "Dashboard":
    total_principal, total_due, total_collected, outstanding = fetch_summary()
    due_today, due_7_days, due_30_days, upcoming_rows = fetch_upcoming_collections()
    cash_on_hand = get_cash_on_hand()

    st.subheader("Portfolio Snapshot")
    st.write(f"**Today:** {date.today().strftime('%B %d, %Y')}")

    a1, a2, a3 = st.columns(3)
    a1.metric("Total Lent", f"₱{total_principal:,.2f}")
    a2.metric("Total Due", f"₱{total_due:,.2f}")
    a3.metric("Cash on Hand", f"₱{cash_on_hand:,.2f}")

    b1, b2 = st.columns(2)
    b1.metric("Collected", f"₱{total_collected:,.2f}")
    b2.metric("Outstanding", f"₱{outstanding:,.2f}")

    st.subheader("Upcoming Collection")
    c1, c2, c3 = st.columns(3)
    c1.metric("Due Today", f"₱{due_today:,.2f}")
    c2.metric("Next 7 Days", f"₱{due_7_days:,.2f}")
    c3.metric("Next 30 Days", f"₱{due_30_days:,.2f}")

    st.write(f"**Available budget for new loaners:** ₱{cash_on_hand:,.2f}")

    with st.expander("Add cash out of pocket"):
        cash_date = st.date_input("Cash Date", value=datetime.today(), key="cash_date")
        cash_amount = st.number_input("Amount", min_value=0.0, step=100.0, key="cash_amount")
        cash_note = st.text_input("Reference / Note", key="cash_note")

        if st.button("Add Cash", key="btn_add_cash"):
            if cash_amount <= 0:
                st.error("Enter a valid amount.")
            else:
                ref = cash_note.strip() if cash_note.strip() else "Owner cash in"
                add_cash_entry(cash_date.strftime("%Y-%m-%d"), "CASH_IN", cash_amount, ref)
                st.success("Cash added.")
                st.rerun()

    st.write("### Upcoming Schedule")
    if upcoming_rows:
        for due_date, full_name, amount in upcoming_rows:
            st.write(f"{due_date} | {full_name} | ₱{amount:,.2f}")
    else:
        st.info("No upcoming unpaid collections found.")

elif menu == "Load Current Data":
    st.subheader("Load your current ledger")
    st.write("This will insert the borrowers, loans, schedules, and cash movements.")
    if st.button("Load Sample Data"):
        seed_current_data()
        ensure_all_loans_have_schedule()
        st.success("Current lending records loaded.")
        st.rerun()

elif menu == "Add Borrower / Loan":
    st.subheader("Add new borrower and loan")

    borrower_name = st.text_input("Borrower Name")
    principal = st.number_input("Principal", min_value=0.0, step=100.0)
    term_months = st.selectbox("Term (months)", [1, 2, 3, 4, 6])
    start_date = st.date_input("Start Date", value=datetime.today())

    current_cash = get_cash_on_hand()
    st.write(f"**Cash on Hand:** ₱{current_cash:,.2f}")

    if principal > 0:
        total_due, preview_schedule = generate_payment_schedule(
            principal,
            term_months,
            start_date.strftime("%Y-%m-%d")
        )
        st.write(f"**Total Due:** ₱{total_due:,.2f}")
        st.write("### Schedule Preview")
        for due_date, amount, _ in preview_schedule:
            st.write(f"{due_date} | ₱{amount:,.2f}")

        projected_balance = current_cash - principal
        st.write(f"**Cash after release:** ₱{projected_balance:,.2f}")

        if principal > current_cash:
            st.error("Not enough cash on hand for this loan.")

    if st.button("Save Borrower + Loan"):
        if not borrower_name.strip():
            st.error("Enter borrower name.")
        elif principal <= 0:
            st.error("Enter principal.")
        elif principal > current_cash:
            st.error("Loan amount exceeds cash on hand.")
        else:
            loan_id = create_manual_loan(
                borrower_name.strip(),
                principal,
                start_date.strftime("%Y-%m-%d"),
                term_months
            )
            st.success(f"Borrower and loan saved. Loan ID: {loan_id}")
            st.rerun()

elif menu == "View Loans":
    st.subheader("Loans")

    loans = fetch_loans()
    if not loans:
        st.info("No loans found.")
    else:
        for loan_id, name, principal, term_months, total_due, start_date, paid_so_far in loans:
            remaining = total_due - paid_so_far

            with st.expander(f"{name} | ₱{principal:,.2f} | Start {start_date}"):
                st.write(f"Loan ID: {loan_id}")
                st.write(f"Principal: ₱{principal:,.2f}")
                st.write(f"Term: {term_months} month(s)")
                st.write(f"Total Due: ₱{total_due:,.2f}")
                st.write(f"Paid So Far: ₱{paid_so_far:,.2f}")
                st.write(f"Remaining: ₱{remaining:,.2f}")

                payments = fetch_payments_for_loan(loan_id)
                if payments:
                    st.write("### Schedule / Payments")
                    for _, due_date, amount, status, paid_at in payments:
                        if paid_at:
                            st.write(f"{due_date} | ₱{amount:,.2f} | {status} | Posted: {paid_at}")
                        else:
                            st.write(f"{due_date} | ₱{amount:,.2f} | {status}")
                else:
                    st.write("No payment schedule yet.")

elif menu == "Post Payment":
    st.subheader("Post Payment")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT l.loan_id, b.full_name, l.principal, l.total_due, l.start_date, l.term_months
        FROM loans l
        JOIN borrowers b ON b.borrower_id = l.borrower_id
        ORDER BY b.full_name
    """)
    loans = cur.fetchall()

    if not loans:
        st.info("No loans found.")
    else:
        loan_dict = {
            f"{name} (Loan {loan_id})": {
                "loan_id": loan_id,
                "name": name,
                "principal": principal,
                "total_due": total_due,
                "start_date": start_date,
                "term_months": term_months
            }
            for loan_id, name, principal, total_due, start_date, term_months in loans
        }

        selected = st.selectbox("Select Borrower Loan", list(loan_dict.keys()))
        selected_loan = loan_dict[selected]
        loan_id = selected_loan["loan_id"]

        ensure_schedule_for_loan(
            loan_id,
            selected_loan["principal"],
            selected_loan["term_months"],
            selected_loan["start_date"]
        )

        cur.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM payments
            WHERE loan_id = ? AND status = 'Paid'
        """, (loan_id,))
        paid_so_far = cur.fetchone()[0] or 0
        remaining = selected_loan["total_due"] - paid_so_far

        cur.execute("""
            SELECT due_date, amount
            FROM payments
            WHERE loan_id = ? AND status != 'Paid'
            ORDER BY due_date
            LIMIT 1
        """, (loan_id,))
        next_due = cur.fetchone()

        st.write("### Loan Summary")

        c1, c2, c3 = st.columns(3)
        c1.metric("Loan Amount", f"₱{selected_loan['principal']:,.2f}")
        c2.metric("Paid So Far", f"₱{paid_so_far:,.2f}")
        c3.metric("Remaining", f"₱{remaining:,.2f}")

        c4, c5 = st.columns(2)
        c4.write(f"**Start Date:** {selected_loan['start_date']}")
        c5.write(f"**Total Due:** ₱{selected_loan['total_due']:,.2f}")

        if next_due:
            st.info(f"Next Due: {next_due[0]} | Amount: ₱{next_due[1]:,.2f}")
        else:
            st.success("Fully Paid")

        st.write("### Schedule")

        cur.execute("""
            SELECT payment_id, due_date, amount, status, paid_at
            FROM payments
            WHERE loan_id = ?
            ORDER BY due_date
        """, (loan_id,))
        payments = cur.fetchall()

        for payment_id, due_date, amount, status, paid_at in payments:
            col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 2])

            col1.write(due_date)
            col2.write(f"₱{amount:,.2f}")
            col3.write(status)
            col4.write(paid_at if paid_at else "-")

            if status != "Paid":
                if col5.button("Mark Paid", key=f"pay_{payment_id}"):
                    posted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    cur.execute("""
                        UPDATE payments
                        SET status = 'Paid',
                            paid_at = ?
                        WHERE payment_id = ?
                    """, (posted_at, payment_id))
                    conn.commit()

                    ref = f"PaymentID {payment_id}"
                    if not cash_entry_exists("COLLECTION", ref):
                        add_cash_entry(date.today().strftime("%Y-%m-%d"), "COLLECTION", amount, ref)

                    st.success("Payment recorded and added to cash on hand.")
                    st.rerun()
            else:
                col5.write("")

    conn.close()

elif menu == "Cash Ledger":
    st.subheader("Cash Ledger")
    st.write(f"**Cash on Hand:** ₱{get_cash_on_hand():,.2f}")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT entry_date, entry_type, amount, reference
        FROM cash_ledger
        ORDER BY entry_date DESC, entry_id DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if rows:
        for entry_date, entry_type, amount, reference in rows:
            st.write(f"{entry_date} | {entry_type} | ₱{amount:,.2f} | {reference}")
    else:
        st.info("No cash entries yet.")