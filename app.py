from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pandas as pd
from pathlib import Path
from werkzeug.utils import secure_filename
import io
import os


app = Flask(__name__)
# limit uploads to 50 MB to avoid accidental huge file uploads
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
CORS(app)

# Fake database (for hackathon)
violations = []

# Try to load dataset from Downloads (change path if you moved the file)
def load_dataset(path, clear_existing=False):
    p = Path(path)
    if not p.exists():
        print(f"Dataset not found: {path}")
        return False
    try:
        # optionally clear existing in-memory violations
        if clear_existing:
            violations.clear()
        # choose reader based on extension
        if p.suffix.lower() == '.csv':
            df = pd.read_csv(p)
        else:
            # Excel (default)
            df = pd.read_excel(p, engine='openpyxl')
        # normalize column names
        df.columns = [str(c).strip() for c in df.columns]

        def pick_field(row, variants):
            for k in variants:
                if k in row and pd.notna(row[k]):
                    return row[k]
            return None

        records = []
        for r in df.to_dict(orient="records"):
            vehicle = pick_field(r, ['vehicle','Vehicle','Vehicle No','vehicle_no','plate','Plate','RegistrationNo','Registration No'])
            vtype = pick_field(r, ['type','Type','Violation Type','violation','Violation','ViolationType'])
            location = pick_field(r, ['location','Location','location_name','Location Name','place','Place'])
            date = pick_field(r, ['date','Date','date_of_violation','Date of Violation','violation_date','Violation Date'])
            lat = pick_field(r, ['latitude', 'Latitude', 'lat', 'Lat'])
            lon = pick_field(r, ['longitude', 'Longitude', 'lon', 'Lon', 'long', 'Long'])

            # build normalized record keeping other fields too
            normalized = {
                'vehicle': str(vehicle).strip() if vehicle is not None else '',
                'type': str(vtype).strip() if vtype is not None else '',
                'location': str(location).strip() if location is not None else '',
                'date': str(date).strip() if date is not None else '',
                'latitude': lat,
                'longitude': lon
            }
            # include other fields
            for k, val in r.items():
                if pd.isna(val):
                    continue
                if k in ['vehicle','Vehicle','Vehicle No','vehicle_no','plate','Plate','RegistrationNo','Registration No',
                         'type','Type','Violation Type','violation','Violation','ViolationType',
                         'location','Location','location_name','Location Name','place','Place',
                         'latitude', 'Latitude', 'lat', 'Lat',
                         'longitude', 'Longitude', 'lon', 'Lon', 'long', 'Long']:
                    continue
                normalized[k] = val
            records.append(normalized)

        violations.extend(records)
        print(f"Loaded {len(records)} records from {path}")
        return True
    except Exception as e:
        print(f"Error loading dataset {path}: {e}")
        return False

# Try local dataset files first, then fallback to Downloads
if not load_dataset('dataset.xlsx', clear_existing=True):
    if not load_dataset('dataset.csv', clear_existing=True):
        print("No default dataset found (dataset.xlsx or dataset.csv). Please upload one.")

@app.route("/")
def home():
    # Serve the frontend dashboard (index.html) directly from the project root
    try:
        return send_from_directory('.', 'index.html')
    except Exception:
        return "Smart Traffic Violation Backend Running"

@app.route('/dataset')
def dataset_page():
    try:
        return send_from_directory('.', 'dataset.html')
    except Exception:
        return "Dataset page not found"


@app.route("/violations", methods=["GET"])
def get_violations():
    # Ensure we have data if the server restarted or initial load failed
    if not violations:
        if not load_dataset('dataset.xlsx', clear_existing=True):
            load_dataset('dataset.csv', clear_existing=True)
    return jsonify(violations)

@app.route("/analytics", methods=["GET"])
def analytics():
    # Ensure we have data if the server restarted or initial load failed
    if not violations:
        if not load_dataset('dataset.xlsx', clear_existing=True):
            load_dataset('dataset.csv', clear_existing=True)

    # Filter by date range if provided
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    data_source = violations

    if start_date or end_date:
        try:
            sd = pd.to_datetime(start_date) if start_date else None
            # Set end date to end of that day
            ed = (pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)) if end_date else None
            
            filtered = []
            for v in data_source:
                try:
                    v_date = pd.to_datetime(v.get('date'))
                    if sd and v_date < sd: continue
                    if ed and v_date > ed: continue
                    filtered.append(v)
                except (ValueError, TypeError):
                    continue
            data_source = filtered
        except Exception as e:
            print(f"Date filter error: {e}")

    total = len(data_source)
    by_type = {}
    by_location = {}
    location_coords = {}
    by_date = {}
    by_hour = {i: 0 for i in range(24)}
    
    # Safety Stats (2017 vs 2018)
    safety_stats = {
        "accidents_2017": 0, "killed_2017": 0, "injured_2017": 0,
        "accidents_2018": 0, "killed_2018": 0, "injured_2018": 0
    }

    for v in data_source:
        # Type Analysis
        t = v.get('type') or v.get('Type') or v.get('Violation Type') or 'Unknown'
        t = str(t).strip() or 'Unknown'
        by_type[t] = by_type.get(t, 0) + 1

        # Location Analysis (High-Risk Zones)
        loc = v.get('location') or 'Unknown'
        loc = str(loc).strip() or 'Unknown'
        if loc:
            by_location[loc] = by_location.get(loc, 0) + 1
            # Capture coordinates if available
            if loc not in location_coords:
                lat = v.get('latitude')
                lon = v.get('longitude')
                if lat is not None and lon is not None:
                    try:
                        location_coords[loc] = {'lat': float(lat), 'lng': float(lon)}
                    except (ValueError, TypeError):
                        pass

        # Date Analysis
        d = v.get('date') or 'Unknown'
        d = str(d).strip() or 'Unknown'
        by_date[d] = by_date.get(d, 0) + 1
        
        # Hour Analysis
        try:
            if d != 'Unknown':
                dt = pd.to_datetime(d)
                if pd.notna(dt):
                    by_hour[dt.hour] += 1
        except (ValueError, TypeError):
            pass
            
        # Aggregate Safety Data (if columns exist in dataset)
        try:
            safety_stats["accidents_2017"] += float(v.get("Number of Accidents - 2017", 0) or 0)
            safety_stats["killed_2017"] += float(v.get("Persons Killed - 2017", 0) or 0)
            safety_stats["injured_2017"] += float(v.get("Persons Injured - 2017", 0) or 0)
            safety_stats["accidents_2018"] += float(v.get("Number of Accidents - 2018", 0) or 0)
            safety_stats["killed_2018"] += float(v.get("Persons Killed - 2018", 0) or 0)
            safety_stats["injured_2018"] += float(v.get("Persons Injured - 2018", 0) or 0)
        except (ValueError, TypeError):
            pass

    over_speed = sum(cnt for k, cnt in by_type.items() if 'speed' in k.lower())
    signal_jump = sum(cnt for k, cnt in by_type.items() if 'signal' in k.lower())

    # Identify High-Risk Zones (Black Spots)
    sorted_locations = sorted(by_location.items(), key=lambda x: x[1], reverse=True)
    high_risk_zones = []
    for k, v in sorted_locations[:5]:
        zone = {"location": k, "count": v}
        if k in location_coords:
            zone['coordinates'] = location_coords[k]
        high_risk_zones.append(zone)

    # Calculate % Changes
    def calc_pct(new, old):
        return ((new - old) / old * 100) if old != 0 else 0.0

    safety_stats["pct_accidents"] = calc_pct(safety_stats["accidents_2018"], safety_stats["accidents_2017"])
    safety_stats["pct_killed"] = calc_pct(safety_stats["killed_2018"], safety_stats["killed_2017"])
    safety_stats["pct_injured"] = calc_pct(safety_stats["injured_2018"], safety_stats["injured_2017"])

    # Generate Recommendations
    recommendations = []
    if total > 0:
        if (over_speed / total) > 0.15:
            recommendations.append("High rate of speeding violations detected. Recommend installing automated speed enforcement cameras.")
        if (signal_jump / total) > 0.15:
            recommendations.append("Frequent signal violations observed. Suggest improving signal visibility or adding red-light cameras.")
    
    if high_risk_zones:
        top_zone = high_risk_zones[0]['location']
        recommendations.append(f"Priority Action: Increase patrol presence at high-risk zone '{top_zone}'.")

    return jsonify({
        "total_violations": total,
        "by_type": by_type,
        "by_location": by_location,
        "by_date": by_date,
        "by_hour": by_hour,
        "high_risk_zones": high_risk_zones,
        "over_speed": over_speed,
        "signal_jump": signal_jump,
        "recommendations": recommendations,
        "safety_stats": safety_stats
    })

@app.route('/upload-dataset', methods=['POST'])
def upload_dataset():
    if 'file' not in request.files:
        return jsonify({'uploaded': False, 'error': 'no file part'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'uploaded': False, 'error': 'no file selected'}), 400

    # mode: 'replace' (default) or 'append'
    mode = (request.form.get('mode') or 'replace').lower()
    replace = (mode != 'append')

    filename = secure_filename(f.filename)
    upload_dir = Path('uploads')
    upload_dir.mkdir(exist_ok=True)
    dest = upload_dir / filename

    # Save the incoming file first
    try:
        f.save(str(dest))
    except Exception as e:
        print('Failed to save uploaded file:', e)
        return jsonify({'uploaded': False, 'error': 'failed to save uploaded file'}), 500

    # Try to load/parse the dataset; if it fails, return an error to the client with details
    ok = load_dataset(str(dest), clear_existing=replace)
    if not ok:
        # keep file for debugging but inform the client
        return jsonify({'uploaded': False, 'error': 'failed to parse dataset', 'path': str(dest)}), 400

    return jsonify({'uploaded': True, 'path': str(dest), 'total': len(violations), 'mode': 'append' if not replace else 'replace'})


@app.route('/reload-dataset', methods=['POST'])
def reload_dataset():
    data = request.json or {}
    path = data.get('path') or 'dataset.xlsx'
    clear = data.get('clear', True)
    ok = load_dataset(path, clear_existing=clear)
    return jsonify({ 'loaded': ok, 'path': path })


@app.route('/clear-dataset', methods=['POST'])
def clear_dataset():
    """Clear all in-memory violations. Optionally delete uploaded files if 'delete_files' provided."""
    data = request.json or {}
    delete_files = bool(data.get('delete_files'))
    violations.clear()
    if delete_files:
        upload_dir = Path('uploads')
        if upload_dir.exists() and upload_dir.is_dir():
            for f in upload_dir.iterdir():
                try:
                    f.unlink()
                except Exception as e:
                    print('Failed to remove', f, e)
    return jsonify({'cleared': True, 'total': len(violations)})


@app.route('/uploaded-files', methods=['GET'])
def uploaded_files():
    """Return list of files in the uploads directory and a count."""
    upload_dir = Path('uploads')
    files = []
    if upload_dir.exists() and upload_dir.is_dir():
        for f in upload_dir.iterdir():
            if f.is_file():
                files.append(str(f.name))
    return jsonify({'count': len(files), 'files': files})


@app.route('/delete-uploaded-files', methods=['POST'])
def delete_uploaded_files():
    """Delete all files in the uploads directory and return what was deleted."""
    upload_dir = Path('uploads')
    deleted = []
    errors = {}
    if upload_dir.exists() and upload_dir.is_dir():
        for f in list(upload_dir.iterdir()):
            try:
                if f.is_file():
                    f.unlink()
                    deleted.append(str(f.name))
            except Exception as e:
                errors[str(f.name)] = str(e)
    return jsonify({'deleted': len(deleted), 'files': deleted, 'errors': errors})


@app.route('/preview-dataset', methods=['POST'])
def preview_dataset():
    """Accepts a multipart file upload and returns a small JSON preview.
    For CSV: returns columns and up to 20 rows.
    For XLSX: returns sheet names and up to 10 rows per sheet.
    """
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'no file part'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'ok': False, 'error': 'no file selected'}), 400

    filename = secure_filename(f.filename)
    name = filename.lower()

    try:
        data = f.read()
        bio = io.BytesIO(data)

        if name.endswith('.csv'):
            # read CSV with pandas (safely) and return small preview
            try:
                df = pd.read_csv(bio, nrows=20)
            except Exception as e:
                return jsonify({'ok': False, 'error': 'failed to parse CSV', 'message': str(e)}), 400
            cols = [str(c) for c in df.columns]
            rows = df.fillna('').values.tolist()[:10]
            return jsonify({'ok': True, 'type': 'csv', 'columns': cols, 'rows': rows})

        elif name.endswith('.xlsx') or name.endswith('.xls'):
            try:
                # use pandas to read all sheets in memory but only return small samples
                sheets = pd.read_excel(bio, sheet_name=None, engine='openpyxl')
            except Exception as e:
                return jsonify({'ok': False, 'error': 'failed to parse XLSX', 'message': str(e)}), 400
            result = {'ok': True, 'type': 'xlsx', 'sheets': {}}
            for sname, df in sheets.items():
                cols = [str(c) for c in df.columns]
                rows = df.fillna('').values.tolist()[:10]
                result['sheets'][sname] = {'columns': cols, 'rows': rows, 'total_rows': len(df)}
            return jsonify(result)
        else:
            return jsonify({'ok': False, 'error': 'unsupported file type'}), 400
    except Exception as e:
        print('Preview error:', e)
        return jsonify({'ok': False, 'error': 'preview failed', 'message': str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
