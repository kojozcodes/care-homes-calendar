import streamlit as st
import pandas as pd
import datetime as dt
import calendar
from io import BytesIO
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import Color, black, white
import textwrap
import re
import requests

# -------------------------
# Streamlit page setup
# -------------------------
st.set_page_config(page_title="Care Home Monthly Calendar", layout="wide")

# -------------------------
# Utility functions
# -------------------------
def parse_csv(uploaded_file):
    if uploaded_file is None:
        return None
    try:
        return pd.read_csv(uploaded_file)
    except Exception as e:
        st.error(f"CSV parse error: {e}")
        return None

def month_date_range(year: int, month: int):
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    return first, last

def clean_text(s):
    if not isinstance(s, str):
        s = str(s) if s is not None else ""
    replacements = {
        "\u2013": "-", "\u2014": "-",
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2026": "...", "\xa0": " ",
    }
    for bad, good in replacements.items():
        s = s.replace(bad, good)
    s = re.sub(r"[^\x00-\x7F]+", "", s)
    return s.strip()

# -------------------------
# Holiday fetcher
# -------------------------
def fetch_uk_bank_holidays(year, month):
    """Fetch UK national bank holidays for the given year/month."""
    try:
        resp = requests.get("https://www.gov.uk/bank-holidays.json", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        st.warning(f"Could not fetch UK bank holidays: {e}")
        return []

    holidays_list = []
    for region in ("england-and-wales", "scotland", "northern-ireland"):
        events = data.get(region, {}).get("events", [])
        for ev in events:
            try:
                d = dt.datetime.strptime(ev["date"], "%Y-%m-%d").date()
            except:
                continue
            if d.year == year and d.month == month:
                holidays_list.append({"date": d, "title": clean_text(ev["title"]), "notes": "Holiday"})
    return holidays_list

def fetch_awareness_days(year, month):
    """Static fallback list of awareness / global days for testing. Expand as needed."""
    # Example list (you should expand with full month data)
    static_list = [
        {"date": dt.date(year, 11, 1), "title": "World Vegan Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 1), "title": "All Saints' Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 2), "title": "All Souls’ Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 5), "title": "Bonfire Night (UK)", "notes": "Holiday"},
        {"date": dt.date(year, 11, 11), "title": "Remembrance Day (UK)", "notes": "Holiday"},
        {"date": dt.date(year, 11, 13), "title": "World Kindness Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 14), "title": "World Diabetes Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 16), "title": "International Day for Tolerance", "notes": "Holiday"},
        {"date": dt.date(year, 11, 19), "title": "International Men’s Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 20), "title": "Universal Children’s Day", "notes": "Holiday"},
        {"date": dt.date(year, 11, 25), "title": "International Day for the Elimination of Violence Against Women", "notes": "Holiday"},
        {"date": dt.date(year, 11, 30), "title": "St Andrew’s Day (Scotland)", "notes": "Holiday"},
    ]
    # Filter for correct year/month
    return [ev for ev in static_list if ev["date"].year == year and ev["date"].month == month]

# -------------------------
# Core: Build calendar day mapping
# -------------------------
def seat_activity_into_calendar(year, month, activities_df, rota_df, rules, include_holidays=True):
    first, last = month_date_range(year, month)
    daymap = {first + dt.timedelta(days=i): [] for i in range((last - first).days + 1)}

    # 1️⃣ Holidays (auto-fetch)
    if include_holidays:
        seen_holidays = set()  # to track (date, normalized_title)

        # Combine all sources
        combined_holidays = fetch_uk_bank_holidays(year, month) + fetch_awareness_days(year, month)

        for ev in combined_holidays:
            d = ev["date"]
            title_norm = clean_text(ev["title"]).strip().lower()

            # Skip duplicates by date + normalized title
            if (d, title_norm) in seen_holidays:
                continue
            seen_holidays.add((d, title_norm))

            if d in daymap:
                daymap[d].append({
                    "time": None,
                    "title": ev["title"],
                    "notes": "Holiday"
                })


    # 2️⃣ Staff Shifts
    if rota_df is not None:
        for _, r in rota_df.iterrows():
            try:
                d = pd.to_datetime(r.get("date")).date()
            except:
                continue
            if d in daymap:
                staff = clean_text(str(r.get("staff", "")))
                staff = re.sub(r"\s*\d+$", "", staff)
                start = str(r.get("shift_start", "")).strip()
                end = str(r.get("shift_end", "")).strip()
                shift_time = f"({start} – {end})" if start and end else ""
                display = f"{staff} {shift_time}".strip()
                if display:
                    daymap[d].append({"time": None, "title": display, "notes": "staff shift"})

    # 3️⃣ Fixed Weekly Rules
    fixed_rules = []
    for rule in rules:
        for d in daymap:
            if d.weekday() == rule["weekday"]:
                fixed_rules.append({"date": d, "time": rule.get("time"), "title": clean_text(rule["title"]), "notes": "fixed"})

    # 4️⃣ Regular Activities
    activities = []
    if activities_df is not None:
        for _, r in activities_df.iterrows():
            name = clean_text(r.get("name") or r.get("activity_name") or "")
            pref_days = str(r.get("preferred_days", "")).split(";")
            pref_days = [p.strip()[:3].lower() for p in pref_days if p.strip()]
            pref_time = str(r.get("preferred_time", "")).strip()
            freq = int(r.get("frequency", 0)) if str(r.get("frequency", "")).isdigit() else 0
            placed = 0
            for d in sorted(daymap.keys()):
                if freq and placed >= freq:
                    break
                dow3 = calendar.day_name[d.weekday()][:3].lower()
                if dow3 in pref_days:
                    activities.append({"date": d, "time": pref_time, "title": name, "notes": "activity"})
                    placed += 1

    # 5️⃣ Merge + Normalize Times + Deduplicate + Sort
    time_pattern = re.compile(r"^(\d{1,2})(?::?(\d{2}))?$")
    def normalize_time(t):
        if not t or not isinstance(t, str):
            return None
        t2 = t.strip().lower().replace(".", ":").replace(" ", "")
        match = time_pattern.match(t2)
        if match:
            hour, minute = match.groups()
            hour = hour.zfill(2)
            minute = minute if minute else "00"
            return f"{hour}:{minute}"
        return None

    all_events = fixed_rules + activities
    for ev in all_events:
        ev["time"] = normalize_time(ev.get("time"))

    for ev in all_events:
        d = ev["date"]
        if d not in daymap:
            continue
        title_norm = ev["title"].lower().strip()
        time_norm = ev.get("time")
        duplicates = [e for e in daymap[d] if e["title"].lower().strip() == title_norm]
        if duplicates:
            has_exact = any(e.get("time") == time_norm for e in duplicates)
            has_proper = any(e.get("time") and len(e.get("time")) == 5 for e in duplicates)
            if has_exact or (has_proper and not time_norm):
                continue
        daymap[d].append({"time": time_norm, "title": ev["title"], "notes": ev["notes"]})

    def sort_key(e):
        t = e.get("time")
        if not t:
            return dt.time(23, 59)
        try:
            h, m = map(int, t.split(":"))
            return dt.time(h, m)
        except:
            return dt.time(23, 59)

    for d in daymap:
        daymap[d].sort(key=lambda e: (
            0 if e["notes"] == "Holiday" else
            1 if e["notes"] == "staff shift" else
            2, sort_key(e)
        ))
    return daymap

# (— rest of your draw_calendar_pdf and Streamlit UI code unchanged — as in your existing)
# You would paste the draw_calendar_pdf definition here and the Streamlit UI as you already have.



def draw_calendar_pdf(title, disclaimer, year, month, cell_texts, background_bytes=None):
    """Generate styled non-editable A3 calendar PDF with improved readability and formatting"""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(A3))
    width, height = landscape(A3)

    # --------------------------
    # Background
    # --------------------------
    if background_bytes:
        try:
            img = ImageReader(BytesIO(background_bytes))
            c.drawImage(img, 0, 0, width=width, height=height, preserveAspectRatio=False, mask="auto")
        except Exception as e:
            st.warning(f"Background load failed: {e}")

    # --------------------------
    # Header (Month & Year + Disclaimer with pill background)
    # --------------------------
    title_text = clean_text(title)
    disclaimer_text = clean_text(disclaimer)

    # Measure title width dynamically
    title_font = "Helvetica-Bold"
    title_size = 30
    c.setFont(title_font, title_size)
    title_width = c.stringWidth(title_text, title_font, title_size)

    # Proportional padding (scales with text width, looks balanced)
    padding_ratio = 0.35  # 35% of text width as side padding
    pill_padding = max(20 * mm, title_width * padding_ratio)

    # Keep your existing height, position, and roundness
    pill_w = title_width + pill_padding
    pill_h = 15 * mm                      # your existing height
    pill_y = height - 16 * mm             # your existing vertical position
    pill_x = (width - pill_w) / 2

    # Draw pill (semi-transparent black, rounded)
    pill_color = Color(0, 0, 0, alpha=0.75)
    c.setFillColor(pill_color)
    c.roundRect(pill_x, pill_y, pill_w, pill_h, 8 * mm, fill=1, stroke=0)

    # Draw title text (centered white)
    c.setFillColor(white)
    c.setFont(title_font, title_size)
    c.drawCentredString(width / 2, pill_y + 4 * mm, title_text)

    # Draw disclaimer (just below, black text)
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(black)
    c.drawCentredString(width / 2, pill_y - 5 * mm, disclaimer_text)



    # --------------------------
    # Layout
    # --------------------------
    left, right, top, bottom = 4 * mm, 4 * mm, 37 * mm, 1 * mm
    grid_w = width - left - right
    cols, rows = 7, 5
    col_w = grid_w / cols

    # Weekday header bar (Mon–Sun)
    weekday_bg = Color(0, 0, 0, alpha=0.85)
    bar_height = 8 * mm
    bar_y = height - top + 6 * mm
    c.setFillColor(weekday_bg)
    c.rect(left, bar_y, grid_w, bar_height, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 15)
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, wd in enumerate(weekdays):
        x = left + i * col_w + col_w / 2
        c.drawCentredString(x, bar_y + 2.5 * mm, wd)

    # 🔽 Control gap between bar and top of first calendar row
    bar_gap = 1.5 * mm  # reduce or increase to adjust spacing (try 3–6mm)
    top_of_grid = bar_y - bar_gap

    # Keep grid height consistent below
    grid_h = top_of_grid - bottom
    row_h = grid_h / rows


    # --------------------------
    # Calendar cells
    # --------------------------
    cream = Color(1, 1, 1, alpha=0.93)
    staff_blue = Color(0, 0.298, 0.6)
    month_days = calendar.monthcalendar(year, month)

    for r_idx, week in enumerate(month_days):
        for c_idx, day in enumerate(week):
            if day == 0:
                continue

            d = dt.date(year, month, day)
            x = left + c_idx * col_w
            y = bottom + (rows - 1 - r_idx) * row_h

            # Background + border
            c.setFillColor(cream)
            c.setStrokeColor(black)
            c.roundRect(x, y, col_w, row_h, 5, fill=1, stroke=1)

            # --- Date (top-right, bold)
            c.setFont("Helvetica-Bold", 12)
            c.setFillColor(black)

            # Measure text width so it's properly aligned to the right margin of the cell
            day_str = str(day)
            day_width = c.stringWidth(day_str, "Helvetica-Bold", 12)

            # Position a few millimetres from the right edge and near the top
            c.drawString(x + col_w - day_width - 3 * mm, y + row_h - 6 * mm, day_str)


            # --- Prepare text
            lines = cell_texts.get(d, "").split("\n")
            text_y = y + row_h - 6 * mm
            line_spacing = 4 * mm  # more readable spacing

            for line in lines:
                line = clean_text(line).strip()
                if not line:
                    continue

                # wrap long lines safely at ~31 characters
                wrapped_lines = textwrap.wrap(line, width=31)
                for subline in wrapped_lines:
                    subline = subline.strip()
                    if not subline:
                        continue

                    # 🔹 Holiday lines — bold, left-aligned, wrapped, and underlined
                    # 🔹 Holiday lines — bold, left-aligned, wrapped, and precisely underlined
                    if subline.isupper():
                        c.setFont("Helvetica-Bold", 8.7)
                        c.setFillColor(black)
                        wrapped_holiday = textwrap.wrap(subline, width=28)

                        for wh in wrapped_holiday:
                            wh = wh.strip()
                            if not wh:
                                continue

                            # Draw text
                            c.drawString(x + 2 * mm, text_y, wh)

                            # Draw underline exactly matching the text width
                            text_width = c.stringWidth(wh, "Helvetica-Bold", 8.7)
                            underline_y = text_y - 0.5 * mm
                            c.line(x + 2 * mm, underline_y, x + 2 * mm + text_width, underline_y)

                            # Move down for next line
                            text_y -= line_spacing

                        continue




                    # 🔹 Staff (italic, blue)
                    if subline.lower().startswith("staff:"):
                        c.setFont("Helvetica-Oblique", 10.5)
                        c.setFillColor(staff_blue)
                        c.drawString(x + 2 * mm, text_y, subline)
                        text_y -= line_spacing
                        continue

                    # 🔹 Activities (bold time, normal text)
                    time_match = re.match(r"^(\d{1,2}:\d{2}\s?(?:am|pm|AM|PM)?)\s?(.*)", subline)
                    if time_match:
                        time_part, rest = time_match.groups()
                        c.setFont("Helvetica-Bold", 10.5)
                        c.setFillColor(black)
                        c.drawString(x + 2 * mm, text_y, time_part)
                        time_width = c.stringWidth(time_part + " ", "Helvetica-Bold", 9.5)
                        c.setFont("Helvetica-Bold", 10.5)
                        c.drawString(x + 2 * mm + time_width, text_y, rest)
                    else:
                        c.setFont("Helvetica-Bold", 10.5)
                        c.setFillColor(black)
                        c.drawString(x + 2 * mm, text_y, subline)

                    text_y -= line_spacing
                    if text_y < y + 4 * mm:
                        break


    c.save()
    buffer.seek(0)
    return buffer




# -------------------------
# Streamlit UI
# -------------------------
st.title("🏡 Care Home Monthly Activities — Editable Preview & A3 PDF")

col1, col2 = st.columns(2)
with col1:
    year = st.number_input("Year", 2024, 2035, dt.date.today().year)
    month = st.selectbox("Month", range(1, 13), index=dt.date.today().month - 1,
                         format_func=lambda x: calendar.month_name[x])
with col2:
    title = st.text_input("Calendar Title", f"{calendar.month_name[month]} {year}")
    disclaimer = st.text_input("Disclaimer", "Activities subject to change. Please confirm with staff.")

st.markdown("### 📋 CSV Upload Instructions")

with st.expander("🧑‍💼 Staff Rota CSV Format (Example)"):
    st.write("""
    **Required Headers:**
    - `date` → Date in format `YYYY-MM-DD`
    - `staff` → Staff member’s full name  
    - `shift_start` → Start time (e.g. `09:00`)
    - `shift_end` → End time (e.g. `16:30`)
    - `role` → (Optional) Staff role or position

    **Example:**
    | date       | staff  | shift_start | shift_end | role      |
    |-------------|--------|--------------|------------|-----------|
    | 2025-11-01  | Lucy   | 09:00        | 16:30      | activities     |
    """)

with st.expander("🎯 Activities CSV Format (Example)"):
    st.write("""
    **Required Headers:**
    - `name` → Activity name  
    - `preferred_days` → Day(s) of week, separated by `;` (e.g. `Mon; Wed; Fri`)  
    - `preferred_time` → Start time (e.g. `14:30`)  
    - `frequency` → Number of times per month  
    - `staff_required` → Number of staff required for the activity  
    - `notes` → (Optional) Any notes or description  

    **Example:**
    | name             | preferred_days | preferred_time | frequency | staff_required | notes                    |
    |------------------|----------------|----------------|------------|----------------|---------------------------|
    | Coffee & Chat      | Mon;Wed;Fri;Sun            | 11:00          | 12          | 1              | Social session with refreshments  |
    """)

rota_df = parse_csv(st.file_uploader("📂 Upload Staff Rota CSV", type=["csv"]))
activities_df = parse_csv(st.file_uploader("📂 Upload Activities CSV", type=["csv"]))

bg_file = st.file_uploader("Background Image (optional)", type=["png", "jpg", "jpeg"])

fixed_rules_text = st.text_area(
    "Fixed Weekly Rules (e.g. Film Night:Thu:18:00)",
    "Film Night:Thu:18:00\nDogs for Health:Thu:11:00\nReminiscence:Sat:18:00"
)

rules = []
for line in fixed_rules_text.splitlines():
    parts = [p.strip() for p in line.split(":")]
    if len(parts) >= 2:
        day = parts[1][:3].lower()
        time = parts[2] if len(parts) > 2 else ""
        title_txt = parts[0]
        weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(day)
        rules.append({"weekday": weekday, "time": time, "title": title_txt})

include_holidays = st.checkbox("Include UK National Holidays", True)

# -------------------------
# Preview and Editable Calendar Section
# -------------------------

# Create a unique session key for each (year, month) combo
session_key = f"{year}-{month:02d}"

if st.button("Preview Calendar"):
    with st.spinner("Generating preview..."):
        daymap = seat_activity_into_calendar(year, month, activities_df, rota_df, rules, include_holidays)
        st.session_state[session_key] = {}

        for d, events in daymap.items():
            lines = []
            for ev in events:
                if ev["notes"] == "Holiday":
                    lines.append(ev["title"].upper())
                elif ev["notes"] == "staff shift":
                    lines.append(f"Staff: {ev['title']}")
                elif ev["notes"] in ("fixed", "activity"):
                    t = ev.get("time", "")
                    lines.append(f"{t} {ev['title']}".strip())
            st.session_state[session_key][d] = "\n".join(lines)

# Editable preview (only for currently selected month)
if session_key in st.session_state:
    st.subheader(f"📝 Edit Calendar for {calendar.month_name[month]} {year} Before Generating PDF")
    month_days = calendar.monthcalendar(year, month)

    for week in month_days:
        cols = st.columns(7)
        for c_idx, day in enumerate(week):
            if day == 0:
                with cols[c_idx]:
                    st.markdown(" ")
                continue
            d = dt.date(year, month, day)
            with cols[c_idx]:
                st.text_area(
                    f"{day}",
                    st.session_state[session_key].get(d, ""),
                    key=f"{session_key}_{d}",
                    height=180,
                )

    # Optional reset button for this month’s edits
    if st.button("🔄 Reset This Month's Edits"):
        st.session_state.pop(session_key, None)
        st.rerun()

    # Generate PDF button
    if st.button("Generate PDF"):
        bg_bytes = bg_file.read() if bg_file else None

        # Gather edited text areas for this month only
        edited_texts = {
            dt.date.fromisoformat(k.split("_")[-1]): v
            for k, v in st.session_state.items()
            if k.startswith(session_key + "_")
        }

        pdf_buf = draw_calendar_pdf(
            title, disclaimer, year, month, edited_texts, background_bytes=bg_bytes
        )

        st.success("✅ A3 PDF calendar generated successfully!")
        st.download_button(
            "📥 Download Calendar (A3 Landscape PDF)",
            data=pdf_buf,
            file_name=f"calendar_{year}_{month:02d}_A3.pdf",
            mime="application/pdf",
        )
