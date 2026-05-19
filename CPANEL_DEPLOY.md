## cPanel Deployment Guide (PHP + Python)

This project can run directly from a PHP page (`index.php`) and execute the Python extractor (`scripts/cpanel_extract.py`) in the same hosting account.

### 1) Upload project files

Upload these folders/files to your cPanel app directory (for example `public_html/pdf-extractor/`):

- `index.php`
- `scripts/`
- `src/`
- `requirements.txt`

You also need writable folders (auto-created by `index.php`, but create manually if permissions are restricted):

- `tmp_uploads/`
- `tmp_results/`

### 2) Create Python environment on cPanel

From cPanel Terminal (or SSH):

```bash
cd ~/public_html/pdf-extractor
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Streamlit subpath (optional)

If you run the Streamlit UI under a URL subpath (not `index.php`), copy
`.streamlit/config.cpanel.toml.example` to `.streamlit/config.toml` on the server.
Do **not** use `baseUrlPath` on [Streamlit Community Cloud](https://streamlit.io/cloud) — it causes health-check 404 errors.

### 3) Set permissions

Ensure PHP can write temporary files:

```bash
chmod -R 775 tmp_uploads tmp_results
```

If needed:

```bash
chown -R <cpanel-user>:<cpanel-user> tmp_uploads tmp_results
```

### 4) PHP settings required

Make sure these are enabled in cPanel/PHP settings:

- `exec()` must be enabled
- `file_uploads = On`
- `upload_max_filesize` and `post_max_size` large enough for your PDF
- `max_execution_time` high enough (the page sets 600s already)

### 5) Run from browser

Open:

`https://your-domain.com/pdf-extractor/index.php`

Then:

1. Upload PDF
2. Select client
3. Enter pages (`22-30` or `14,16,20-25`)
4. Click **Process PDF**

After extraction, UI shows:

- row metrics (Rows, Unique Catalogs, Pages Returned)
- preview table (first 5 rows)
- download buttons (CSV + Excel, when Excel writer is available)

### 6) Troubleshooting

- Add `?debug=1` to URL to see command/debug output.
- If Excel button is missing, CSV still works (Excel dependency may be missing).
- If extraction fails, verify Python path and installed dependencies in `venv`.
