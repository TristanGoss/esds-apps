CREATE TABLE IF NOT EXISTS qr_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_id TEXT UNIQUE NOT NULL,
    target_url TEXT NOT NULL,
    description TEXT CHECK(length(description) <= 1024),
    scan_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
