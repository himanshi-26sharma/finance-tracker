import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from datetime import datetime
from sqlalchemy import func
import os
import re
import io
import base64
import csv
import logging

plt.rcParams['font.family'] = 'DejaVu Sans'
mpl_logger = logging.getLogger('matplotlib')
mpl_logger.setLevel(logging.WARNING)

# Initialize Flask app
app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key-here'  # Change for production!

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('finance_tracker.log'),
        logging.StreamHandler()
    ]
)

# Initialize extensions
csrf = CSRFProtect(app)
db = SQLAlchemy(app)

# Database Model
class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD format
    type = db.Column(db.String(10), nullable=False)  # 'income' or 'expense'

    def __repr__(self):
        return f"<Transaction {self.id}: {self.type} ${self.amount} ({self.category})>"

class Budget(db.Model):
    __tablename__ = 'budgets'
    category = db.Column(db.String(50), primary_key=True)
    limit = db.Column(db.Float, nullable=False)

# Helper Functions
def get_expense_categories(transactions):
    """Categorize and sum expenses"""
    expense_categories = {}
    
    for transaction in transactions:
        if transaction.type == 'expense':
            if transaction.category in expense_categories:
                expense_categories[transaction.category] += transaction.amount
            else:
                expense_categories[transaction.category] = transaction.amount
                
    return expense_categories

def generate_pie_chart(data, title):
    """Generate a base64 encoded pie chart"""
    if not data:
        return None
        
    try:
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.pie(data.values(), labels=data.keys(), autopct='%1.1f%%', 
               startangle=90, colors=['#FF6384', '#36A2EB', '#FFCE56'])
        ax.axis('equal')
        ax.set_title(title)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')
        return image_base64
    finally:
        plt.close(fig)

def get_monthly_trends():
    """Get monthly income/expense totals"""
    return db.session.query(
        func.strftime('%Y-%m', Transaction.date).label('month'),
        Transaction.type,
        func.sum(Transaction.amount).label('total')
    ).group_by('month', 'type').all()

# Database Management Commands
@app.cli.command('reset-db')
def reset_db():
    """Delete and recreate the database"""
    with app.app_context():
        db.drop_all()
        db.create_all()
    print("Database reset complete!")

@app.cli.command('seed-db')
def seed_db():
    """Add test data"""
    with app.app_context():
        test_data = [
            Transaction(amount=1000.0, category="Salary", type="income", date="2023-08-01"),
            Transaction(amount=50.0, category="Groceries", type="expense", date="2023-08-02"),
            Transaction(amount=30.0, category="Transport", type="expense", date="2023-08-03"),
            Transaction(amount=200.0, category="Freelance", type="income", date="2023-08-05")
        ]
        db.session.add_all(test_data)
        db.session.commit()
    print(f"Added {len(test_data)} test transactions")

# Routes        
@app.route('/')
def dashboard():
    try:
        # Force fresh data
        db.session.expire_all()
        all_transactions = Transaction.query.order_by(Transaction.date.desc()).all()
        
        # Calculate totals
        total_income = sum(t.amount for t in all_transactions if t.type == 'income')
        total_expenses = sum(t.amount for t in all_transactions if t.type == 'expense')
        balance = total_income - total_expenses
        
        # Generate expense categories and chart
        expense_categories = get_expense_categories(all_transactions)
        expense_chart = generate_pie_chart(expense_categories, "Expense Distribution") if expense_categories else None
        
        # Budget alerts
        budget_alerts = []
        if Budget.query.count() > 0:
            for budget in Budget.query.all():
                spent = sum(t.amount for t in all_transactions 
                         if t.type == 'expense' and t.category == budget.category)
                if spent > budget.limit:
                    budget_alerts.append(f"Budget exceeded for {budget.category}")
        
        # Always provide colors
        chart_colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF']
        
        return render_template(
            'dashboard.html',
            transactions=all_transactions[:5],
            balance=balance,
            income=total_income,
            expenses=total_expenses,
            expense_categories=expense_categories,
            expense_chart=expense_chart,
            colors=chart_colors,
            budget_alerts=budget_alerts,
            export_enabled='export_csv' in app.view_functions  # Add this line
        )
        
    except Exception as e:
        app.logger.error(f"Dashboard error: {str(e)}", exc_info=True)
        return render_template('dashboard.html',
                            transactions=[],
                            balance=0,
                            income=0,
                            expenses=0,
                            colors=['#FF6384'],  # Default color for error case
                            error_message=str(e))

@app.route('/add', methods=['GET', 'POST'])
def add_transaction():
    if request.method == 'POST':
        form_errors = {}
        transaction_data = {
            'amount': request.form.get('amount', '').strip(),
            'category': request.form.get('category', '').strip(),
            'date': request.form.get('date', '').strip(),
            'type': request.form.get('type', '').strip().lower()
        }

        # Validation
        try:
            amount = float(transaction_data['amount'])
            if amount <= 0:
                form_errors['amount'] = "Amount must be greater than 0"
            elif amount > 1000000:
                form_errors['amount'] = "Amount exceeds maximum limit"
        except (ValueError, TypeError):
            form_errors['amount'] = "Invalid amount (must be a number)"

        if not transaction_data['category']:
            form_errors['category'] = "Category is required"
        elif len(transaction_data['category']) > 50:
            form_errors['category'] = "Category too long (max 50 chars)"
        elif not re.match(r'^[a-zA-Z0-9\s\-]+$', transaction_data['category']):
            form_errors['category'] = "Invalid characters in category"

        try:
            input_date = datetime.strptime(transaction_data['date'], '%Y-%m-%d').date()
            today = datetime.now().date()
            if input_date > today:
                form_errors['date'] = "Date cannot be in the future"
            elif input_date < today.replace(year=today.year-2):
                form_errors['date'] = "Date too far in the past"
        except ValueError:
            form_errors['date'] = "Invalid date format (YYYY-MM-DD required)"

        if transaction_data['type'] not in ['income', 'expense']:
            form_errors['type'] = "Type must be either 'income' or 'expense'"

        if not form_errors:
            try:
                new_transaction = Transaction(
                    amount=round(float(transaction_data['amount']), 2),
                    category=transaction_data['category'],
                    date=transaction_data['date'],
                    type=transaction_data['type']
                )

                db.session.add(new_transaction)
                db.session.commit()

                saved_trans = db.session.get(Transaction, new_transaction.id)
                if not saved_trans:
                    raise RuntimeError("Transaction not persisted to database")

                app.logger.info(f"Transaction added: ID {new_transaction.id} - {new_transaction}")
                flash('Transaction added successfully!', 'success')
                return redirect(url_for('dashboard'))

            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Database error: {str(e)}", exc_info=True)
                form_errors['database'] = "Failed to save transaction. Please try again."
                return render_template('add.html',
                                    form_errors=form_errors,
                                    form_data=transaction_data,
                                    default_date=datetime.now().strftime('%Y-%m-%d'))

        app.logger.warning(f"Form validation failed: {form_errors}")
        return render_template('add.html',
                            form_errors=form_errors,
                            form_data=transaction_data,
                            default_date=datetime.now().strftime('%Y-%m-%d'))

    return render_template('add.html',
                         form_errors={},
                         form_data={},
                         default_date=datetime.now().strftime('%Y-%m-%d'))

@app.route('/delete/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    try:
        transaction = Transaction.query.get_or_404(transaction_id)
        db.session.delete(transaction)
        db.session.commit()
        flash('Transaction deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error deleting transaction', 'error')
        app.logger.error(f"Delete error: {str(e)}")
    return redirect(url_for('dashboard'))

@app.route('/export_csv')
def export_csv():
    """Export transactions as CSV"""
    try:
        transactions = Transaction.query.order_by(Transaction.date).all()
        
        def generate():
            data = io.StringIO()
            writer = csv.writer(data)
            
            # Write header
            writer.writerow(['ID', 'Date', 'Category', 'Type', 'Amount'])
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)
            
            # Write data
            for t in transactions:
                writer.writerow([t.id, t.date, t.category, t.type, t.amount])
                yield data.getvalue()
                data.seek(0)
                data.truncate(0)
        
        response = Response(
            generate(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=transactions.csv"}
        )
        return response
        
    except Exception as e:
        app.logger.error(f"Export error: {str(e)}")
        flash('Error generating export file', 'error')
        return redirect(url_for('dashboard'))

# Initialize database and ensure clean shutdown
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)