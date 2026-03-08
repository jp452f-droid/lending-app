import streamlit as st
import sqlite3
from datetime import datetime, date
import pandas as pd

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


def add_loan(name, principal, start_date, term_months):
    borrower_id = add_borrower_if_missing(name)
    total_due, schedule = generate_payment_schedule(principal, term_months, start_date)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO loans (
            borrower_id, principal, term_months, monthly_interest_rate,
            total_due, start_date, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (borrower_id, principal, term_months, 0.15, total_due, start_date, "active"))
    conn.commit()
    loan_id = cur.lastrowid

    for due_date, amount, status in schedule:
        cur.execute("""
            INSERT INTO payments (loan_id, due_date, amount, status, paid_at)
            VALUES (?, ?, ?, ?, ?)
        """, (loan_id, due_date, amount, status, None))

    conn.commit()
    conn.close()

    add_cash_entry(start_date, "DISBURSEMENT", -principal, f"Loan {loan_id} - {name}")
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


def seed_current_data():
    # Check if data already exists to prevent duplicates
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM borrowers")
    if cur.fetchone()[0] > 5:  # If more than 5 borrowers exist, skip seeding
        conn.close()
        return
    conn.close()
    
    loans = [
        {
            "name": "Wychelle",
            "principal": 5000,
            "start_date": "2026-01-03",
            "term_months": 3,
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
            "payments": []
        },
        {
            "name": "Cza",
            "principal": 3600,
            "start_date": "2026-02-01",
            "term_months": 3,
            "payments": []
        },
    ]

    for item in loans:
        loan_id = add_loan(item["name"], item["principal"], item["start_date"], item["term_months"])

        # overwrite schedule/status for seed data
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM payments WHERE loan_id = ?", (loan_id,))
        conn.commit()
        conn.close()

        if item["payments"]:
            for due_date, amount, status in item["payments"]:
                paid_at = due_date if status == "Paid" else None
                add_payment_if_missing(loan_id, due_date, amount, status, paid_at)
                if status == "Paid":
                    ref = f"PaymentID seed-{loan_id}-{due_date}-{amount}"
                    if not cash_entry_exists("COLLECTION", ref):
                        add_cash_entry(due_date, "COLLECTION", amount, ref)
        else:
            ensure_schedule_for_loan(loan_id, item["principal"], item["term_months"], item["start_date"])


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


# Initialize database
setup_db()

# App title
st.title("Kelsey Lending")
st.caption("Loan Management System")

# Sidebar menu
menu = st.sidebar.selectbox(
    "Menu",
    ["Dashboard", "Load Current Data", "Add Borrower / Loan", "View Loans", "Post Payment", "Cash Ledger"]
)

# Dashboard
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

    # Get upcoming collections with specific dates
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            p.due_date,
            GROUP_CONCAT(b.full_name || ' (₱' || printf('%,.2f', p.amount) || ')', ', ') as borrowers,
            SUM(p.amount) as total
        FROM payments p
        JOIN loans l ON p.loan_id = l.loan_id
        JOIN borrowers b ON l.borrower_id = b.borrower_id
        WHERE p.status != 'Paid' 
            AND p.due_date >= date('now')
        GROUP BY p.due_date
        ORDER BY p.due_date ASC
        LIMIT 4
    """)
    next_collections = cur.fetchall()
    conn.close()

    st.subheader("📅 Upcoming Collection Dates")

    if next_collections:
        # Create columns for each upcoming date
        cols = st.columns(len(next_collections))
        
        for i, (due_date, borrowers, total) in enumerate(next_collections):
            # Parse the date
            due_obj = datetime.strptime(due_date, "%Y-%m-%d").date()
            today = date.today()
            days_away = (due_obj - today).days
            
            # Format the display
            with cols[i]:
                # Date header with days away
                if days_away == 0:
                    date_label = "🔴 TODAY"
                elif days_away == 1:
                    date_label = "⚠️ TOMORROW"
                else:
                    date_label = f"📆 {due_obj.strftime('%b %d')} (in {days_away} days)"
                
                st.markdown(f"**{date_label}**")
                st.markdown(f"### ₱{total:,.2f}")
                
                # Show list of borrowers
                borrower_list = borrowers.split(', ')
                with st.expander(f"View {len(borrower_list)} borrowers"):
                    for borrower in borrower_list:
                        st.write(f"• {borrower}")
    else:
        st.info("No upcoming collections scheduled")

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
# Add Borrower / Loan
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
            st.error("Loan amount exceeds cash on hand.")

    if st.button("Save Borrower + Loan"):
        if not borrower_name.strip():
            st.error("Enter borrower name.")
        elif principal <= 0:
            st.error("Enter principal.")
        elif principal > current_cash:
            st.error("Not enough cash on hand.")
        else:
            loan_id = add_loan(
                borrower_name.strip(),
                principal,
                start_date.strftime("%Y-%m-%d"),
                term_months
            )
            st.success(f"Loan saved. Loan ID: {loan_id}")
            st.rerun()

# View Loans
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

# Post Payment
elif menu == "Post Payment":
    st.subheader("Post Payment")

    loans = fetch_loans()
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
            for loan_id, name, principal, term_months, total_due, start_date, _ in loans
        }

        selected = st.selectbox("Select Borrower Loan", list(loan_dict.keys()))
        selected_loan = loan_dict[selected]
        loan_id = selected_loan["loan_id"]

        paid_so_far = sum(
            amt for _, _, amt, status, _ in fetch_payments_for_loan(loan_id) if status == "Paid"
        )
        remaining = selected_loan["total_due"] - paid_so_far

        next_due = None
        for _, due_date, amount, status, _ in fetch_payments_for_loan(loan_id):
            if status != "Paid":
                next_due = (due_date, amount)
                break

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
        payments = fetch_payments_for_loan(loan_id)

        for payment_id, due_date, amount, status, paid_at in payments:
            col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 2])
            col1.write(due_date)
            col2.write(f"₱{amount:,.2f}")
            col3.write(status)
            col4.write(paid_at if paid_at else "-")

            if status != "Paid":
                if col5.button("Mark Paid", key=f"pay_{payment_id}"):
                    posted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    conn = get_conn()
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE payments
                        SET status = 'Paid', paid_at = ?
                        WHERE payment_id = ?
                    """, (posted_at, payment_id))
                    conn.commit()
                    conn.close()

                    ref = f"PaymentID {payment_id}"
                    if not cash_entry_exists("COLLECTION", ref):
                        add_cash_entry(date.today().strftime("%Y-%m-%d"), "COLLECTION", amount, ref)

                    st.success("Payment posted.")
                    st.rerun()
            else:
                col5.write("")

# ============================================
# ENHANCED CASH LEDGER SECTION - FIXED
# ============================================
elif menu == "Cash Ledger":
    st.subheader("📒 Complete Cash Ledger")
    
    # Get current cash on hand
    current_cash = get_cash_on_hand()
    
    # Summary metrics at the top
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Cash on Hand", f"₱{current_cash:,.2f}")
    
    # Get all ledger entries
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT entry_date, entry_type, amount, reference, entry_id
        FROM cash_ledger
        ORDER BY entry_date ASC, entry_id ASC
    """)
    rows = cur.fetchall()
    
    # Calculate totals - FIXED: No duplicate calculations
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'CASH_IN'")
    total_cash_in = cur.fetchone()[0]
    
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'DISBURSEMENT'")
    disbursement_result = cur.fetchone()[0]
    total_disbursements = abs(disbursement_result)
    
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE entry_type = 'COLLECTION'")
    total_collections = cur.fetchone()[0]
    
    conn.close()
    
    # Summary section
    with st.expander("📊 Ledger Summary", expanded=True):
        sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)
        sum_col1.metric("Total Cash In", f"₱{total_cash_in:,.2f}")
        sum_col2.metric("Total Disbursed", f"₱{total_disbursements:,.2f}")
        sum_col3.metric("Total Collected", f"₱{total_collections:,.2f}")
        sum_col4.metric("Net Flow", f"₱{current_cash:,.2f}")
    
    # Main ledger table
    st.write("### 📋 Transaction History")
    
    if rows:
        # Create ledger data with running balance
        ledger_data = []
        running_balance = 0
        
        for entry_date, entry_type, amount, reference, entry_id in rows:
            running_balance += amount
            
            # Format based on transaction type
            if entry_type == "CASH_IN":
                in_amount = amount
                out_amount = 0
                type_emoji = "💰"
            elif entry_type == "COLLECTION":
                in_amount = amount
                out_amount = 0
                type_emoji = "📥"
            elif entry_type == "DISBURSEMENT":
                in_amount = 0
                out_amount = abs(amount)
                type_emoji = "📤"
            else:
                in_amount = amount if amount > 0 else 0
                out_amount = abs(amount) if amount < 0 else 0
                type_emoji = "🔄"
            
            ledger_data.append({
                "Date": entry_date,
                "Type": f"{type_emoji} {entry_type}",
                "Description": reference,
                "In (₱)": f"₱{in_amount:,.2f}" if in_amount > 0 else "",
                "Out (₱)": f"₱{out_amount:,.2f}" if out_amount > 0 else "",
                "Balance (₱)": f"₱{running_balance:,.2f}"
            })
        
        # Display as dataframe with proper column widths
        df = pd.DataFrame(ledger_data)
        
        # Use columns to control layout
        col_config = {
            "Date": st.column_config.TextColumn("Date", width=100),
            "Type": st.column_config.TextColumn("Type", width=100),
            "Description": st.column_config.TextColumn("Description", width=300),
            "In (₱)": st.column_config.TextColumn("In (₱)", width=100),
            "Out (₱)": st.column_config.TextColumn("Out (₱)", width=100),
            "Balance (₱)": st.column_config.TextColumn("Balance (₱)", width=120),
        }
        
        st.dataframe(
            df,
            column_config=col_config,
            hide_index=True,
            use_container_width=True,
            height=400
        )
        
        # Show beginning and ending balance
        st.caption(f"**Beginning Balance:** ₱0.00 | **Ending Balance:** ₱{running_balance:,.2f}")
        
        # Search box
        search = st.text_input("🔍 Search transactions", placeholder="Type to filter by description...")
        if search:
            filtered = [d for d in ledger_data if search.lower() in d["Description"].lower()]
            if filtered:
                st.write(f"Found {len(filtered)} matching transactions:")
                st.dataframe(pd.DataFrame(filtered), hide_index=True, use_container_width=True)
        
    else:
        st.info("No cash entries yet. Add cash or create loans to see the ledger.")
    
    # Separator
    st.markdown("---")
    
    # ============================================
    # IMPROVED ADD CASH SECTION
    # ============================================
    st.subheader("➕ Record New Transaction")
    
    with st.form("add_cash_ledger", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            entry_date = st.date_input("Transaction Date", value=datetime.today())
            entry_type = st.selectbox(
                "Transaction Type",
                options=[
                    "CASH_IN - Add personal money to business",
                    "COLLECTION - Payment received from borrower", 
                    "DISBURSEMENT - New loan given out",
                    "ADJUSTMENT - Manual correction"
                ]
            )
        
        with col2:
            amount = st.number_input("Amount (₱)", min_value=0.01, step=100.0, format="%.2f")
            reference = st.text_input("Description / Reference", placeholder="e.g., Initial capital, Payment for...")
        
        # Show preview of impact
        if entry_type.startswith("DISBURSEMENT"):
            st.warning(f"⚠️ This will **deduct ₱{amount:,.2f}** from your cash on hand")
            final_amount = -amount
            type_code = "DISBURSEMENT"
        elif entry_type.startswith("CASH_IN"):
            st.success(f"✅ This will **add ₱{amount:,.2f}** to your cash on hand")
            final_amount = amount
            type_code = "CASH_IN"
        elif entry_type.startswith("COLLECTION"):
            st.success(f"✅ This will **add ₱{amount:,.2f}** to your cash on hand")
            final_amount = amount
            type_code = "COLLECTION"
        else:
            direction = st.radio("Direction:", ["Add to cash (Inflow)", "Deduct from cash (Outflow)"], horizontal=True)
            if direction == "Add to cash (Inflow)":
                final_amount = amount
            else:
                final_amount = -amount
            type_code = "ADJUSTMENT"
        
        submitted = st.form_submit_button("💾 Record Transaction", use_container_width=True)
        
        if submitted:
            if not reference:
                reference = "No description"
            
            # Clean up type code
            clean_type = type_code
            
            add_cash_entry(
                entry_date.strftime("%Y-%m-%d"),
                clean_type,
                final_amount,
                reference
            )
            st.success(f"✅ Transaction recorded!")
            st.rerun()