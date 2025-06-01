from flask import Flask, request, send_file, render_template, redirect, url_for, session
import pandas as pd
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Needed for session

def convert_dat_to_excel(dat_file):
    column_names = ['User ID', 'Timestamp', 'Col3', 'Col4', 'Col5', 'Col6']
    df = pd.read_csv(dat_file, delimiter='\t', header=None, names=column_names)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
    df = df.dropna(subset=['Timestamp'])
    df['Name'] = ''
    df = df[['User ID', 'Timestamp', 'Name', 'Col3', 'Col4', 'Col5', 'Col6']]
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Raw Attendance')
    output.seek(0)
    return output

def adjust_time_for_user_60(df):
    mask = (df['User ID'] == 60) & \
           (df['Timestamp'].dt.time >= datetime.strptime('10:45', '%H:%M').time()) & \
           (df['Timestamp'].dt.time <= datetime.strptime('10:48', '%H:%M').time())
    df.loc[mask, 'Timestamp'] = df.loc[mask, 'Timestamp'].apply(
        lambda ts: ts.replace(hour=10, minute=45, second=0, microsecond=0)
    )
    return df

def process_attendance_data(excel_file, user_name_map, selected_month, selected_year):
    df = pd.read_excel(excel_file)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    df = adjust_time_for_user_60(df)
    df = df[(df['Timestamp'].dt.month == selected_month) & (df['Timestamp'].dt.year == selected_year)]
    if df.empty:
        return None, None
    df['Date'] = df['Timestamp'].dt.date
    df['Time'] = df['Timestamp'].dt.time
    df['Day'] = df['Timestamp'].dt.day_name()
    df = df.sort_values(['User ID', 'Timestamp'])
    grouped = df.groupby(['User ID', 'Date'])
    result = pd.DataFrame(columns=[
        'Serial No', 'User ID', 'Name', 'Date', 'Day', 'In Time', 'Out Time',
        'Working Hours', 'Attendance Status', 'Short Leave', 'Remarks'])
    summary = pd.DataFrame(columns=[
        'Serial No', 'User ID', 'Name', 'Full Days', 'Half Days',
        'Short Leaves', 'Total Working Days', 'Remarks'])
    serial_no = 1
    for (user_id, date), group in grouped:
        timestamps = group['Timestamp'].tolist()
        remarks = ""
        if len(timestamps) == 1:
            remarks = "Supervision Required - Single Entry"
        in_time = timestamps[0]
        out_time = timestamps[-1] if len(timestamps) > 1 else in_time
        working_hours = (out_time - in_time).total_seconds() / 3600 if len(timestamps) > 1 else 0
        if working_hours < 4:
            status = 'Leave'
        elif 4 <= working_hours < 7.2:
            status = 'Half Day'
        else:
            status = 'Full Day'
        short_leave = 'No'
        in_time_obj = in_time.time()
        out_time_obj = out_time.time()
        condition1 = (datetime.strptime('10:46', '%H:%M').time() <= in_time_obj <= datetime.strptime('11:59', '%H:%M').time() and
                          out_time_obj >= datetime.strptime('19:00', '%H:%M').time())
        condition2 = (datetime.strptime('10:20', '%H:%M').time() <= in_time_obj <= datetime.strptime('10:46', '%H:%M').time() and
                      datetime.strptime('18:00', '%H:%M').time() <= out_time_obj <= datetime.strptime('18:30', '%H:%M').time())
        if condition1 or condition2:
            short_leave = 'Yes'
        day_name = in_time.strftime('%A')
        result.loc[len(result)] = [
            serial_no, user_id, user_name_map.get(user_id, ''), date, day_name,
            in_time.time(), out_time.time(), round(working_hours, 2),
            status, short_leave, remarks
        ]
        serial_no += 1
    unique_users = result['User ID'].unique()
    summary_serial_no = 1
    for user_id in unique_users:
        user_data = result[result['User ID'] == user_id]
        full_days = len(user_data[user_data['Attendance Status'] == 'Full Day'])
        half_days = len(user_data[user_data['Attendance Status'] == 'Half Day'])
        short_leaves = len(user_data[user_data['Short Leave'] == 'Yes'])
        total_working_days = round(full_days + (half_days / 2) - ((short_leaves-2) / 4), 2)
        user_remarks = user_data['Remarks'].unique()
        summary_remark = ""
        if "Supervision Required - Single Entry" in user_remarks:
            summary_remark = "Has incomplete attendance records (single entries)"
        summary.loc[len(summary)] = [
            summary_serial_no, user_id, user_name_map.get(user_id, ''), float(full_days), float(half_days),
            float(short_leaves), float(total_working_days), summary_remark
        ]
        summary_serial_no += 1
    return result, summary

# Store the last processed file in memory (for demo; use session or DB for production)
last_report = {}

@app.route('/', methods=['GET', 'POST'])
def upload():
    global last_report
    # Generate last 12 months for dropdown
    now = datetime.now()
    months = []
    for i in range(12):
        dt = (now.replace(day=1) - pd.DateOffset(months=i))
        months.append((dt.month, dt.year, dt.strftime('%B %Y')))
    months.reverse()
    summary_data = None
    selected_month = None
    selected_year = None
    if request.method == 'POST':
        file = request.files['file']
        month_year = request.form['month']
        selected_month, selected_year = [int(x) for x in month_year.split('-')]
        if file.filename.endswith('.dat'):
            excel_file = convert_dat_to_excel(file)
            file = excel_file
        elif not file.filename.endswith('.xlsx'):
            return 'Invalid file format. Please upload a .xlsx or .dat file.'
        try:
            user_db = pd.read_csv('user_database.csv')
            user_name_map = user_db.dropna(subset=['User ID', 'Name']) \
                                   .drop_duplicates(subset=['User ID']) \
                                   .set_index('User ID')['Name'].to_dict()
        except Exception as e:
            return f"Error loading user database: {str(e)}"
        processed_data, summary_data = process_attendance_data(file, user_name_map, selected_month, selected_year)
        if processed_data is None:
            return "No data found for selected month."
        # Remove time from Date column
        if 'Date' in processed_data.columns:
            processed_data['Date'] = processed_data['Date'].astype(str)
        # Save the full report to memory for download
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            processed_data.to_excel(writer, index=False, sheet_name='Processed Attendance')
            summary_data.to_excel(writer, index=False, sheet_name='Summary')
        output.seek(0)
        last_report['data'] = output.read()
        last_report['filename'] = f"attendance_report_{selected_month}_{selected_year}.xlsx"
        last_report['summary_data'] = summary_data
        last_report['selected_month'] = selected_month
        last_report['selected_year'] = selected_year
        # Show summary table in browser
        return render_template(
            'index.html',
            months=months,
            summary=summary_data,
            selected_month=selected_month,
            selected_year=selected_year,
            download_available=True
        )
    # GET request
    return render_template('index.html', months=months, download_available=False)

def generate_salary_slip_df(user_row, month, year):
    slip = {
        'Company': ['Future Point'],
        'Address1': ['A-3 First Floor Ring Road'],
        'Address2': ['South Extension Part-l'],
        'Address3': ['NEW DELHI-110049'],
        'Pay Slip For The Month': [f"{datetime(year, month, 1).strftime('%b.%Y')}"],
        'Employee Name': [user_row['Name']],
        'Employee Number': [f"{int(user_row['User ID']):08d}"],
        'Department': [user_row.get('Department', '')],
        'Location': [user_row.get('Location', '')],
        'Previous Leave Bal': [user_row.get('Previous Leave Bal', '')],
        'This Month Leave': [user_row.get('This Month Leave', '')],
        'Leaves Utilized': [user_row.get('Leaves Utilized', '')],
        'Total Leave Bal': [user_row.get('Total Leave Bal', '')],
        'EL': [user_row.get('EL', '')],
        'CL': [user_row.get('CL', '')],
        'Basic Salary': [user_row.get('Basic Salary', 0.0)],
        'House Rent Allowance': [user_row.get('House Rent Allowance', 0.0)],
        'Transport Allowance': [user_row.get('Transport Allowance', 0.0)],
        'Total Earning': [user_row.get('Total Earning', 0.0)],
        'Advance To Staff': [user_row.get('Advance To Staff', 0.0)],
        'ESI': [user_row.get('ESI', 0.0)],
        'Leave Deduction': [user_row.get('Leave Deduction', 0.0)],
        'Total Deduction': [user_row.get('Total Deduction', 0.0)],
        'Net Amount': [user_row.get('Net Amount', 0.0)],
    }
    return pd.DataFrame(slip)

@app.route('/download-report')
def download_report():
    global last_report
    if 'data' in last_report and 'summary_data' in last_report and 'selected_month' in last_report and 'selected_year' in last_report:
        # Recreate the Excel file WITHOUT salary slips
        output = BytesIO()
        processed_data = pd.read_excel(BytesIO(last_report['data']), sheet_name='Processed Attendance')
        summary_data = last_report['summary_data']

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            processed_data.to_excel(writer, index=False, sheet_name='Processed Attendance')
            summary_data.to_excel(writer, index=False, sheet_name='Summary')
            # Salary slip sheets are NOT created here

        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=last_report.get('filename', 'attendance_report.xlsx'),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    return redirect(url_for('upload'))

@app.route('/salary-slip/<int:user_id>/<int:month>/<int:year>', methods=['GET', 'POST'])
def salary_slip(user_id, month, year):
    # Load user and attendance data
    user_db = pd.read_csv('user_database.csv')
    user = user_db[user_db['User ID'] == user_id].iloc[0]
    # Dummy data for demonstration, you should fetch real values as needed
    payslip_data = {
        'company': 'Future Point',
        'address1': 'A-3 First Floor Ring Road',
        'address2': 'South Extension Part-l',
        'address3': 'NEW DELHI-110049',
        'month': month,
        'year': year,
        'employee_name': user['Name'],
        'employee_number': f"{int(user_id):08d}",
        'department': 'Software',
        'location': 'South Ext',
        'previous_leave_bal': 6.00,
        'this_month_leave': 2.00,
        'leaves_utilized': 7.00,
        'total_leave_bal': 1.00,
        'el': 1.00,
        'cl': 0.00,
        'basic_salary': 0.00,
        'hra': 14300.00,
        'ta': 6600.00,
        'total_earning': 22000.00,
        'advance': 0.00,
        'esi': 0.00,
        'leave_deduction': 1100.00,
        'total_deduction': 0.00,
        'net_amount': 22000.00,
    }
    if request.method == 'POST':
        # Update payslip_data with form values
        for key in payslip_data.keys():
            payslip_data[key] = request.form.get(key, payslip_data[key])
        return render_template('salary_slip.html', payslip=payslip_data, printable=True)
    return render_template('salary_slip.html', payslip=payslip_data, printable=False)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
