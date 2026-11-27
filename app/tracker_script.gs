/**
 * JobApp → Google Sheet bridge.  Paste into the tracker sheet: Extensions > Apps Script.
 * Deploy > New deployment > Web app > Execute as: Me, Who has access: Anyone > Deploy.
 *
 * Safety: it only ADDS rows and UPDATES cells in rows JobApp created (matched by the key column).
 * It never deletes rows, never touches formatting, and ignores any request without the secret.
 */
const SECRET = '__SECRET__';        // filled in by JobApp; must match TRACKER_SECRET in .env
const HEADER_ROW = __HEADER_ROW__;  // row number that holds the column headers

function respond_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function sheet_(name) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = name ? ss.getSheetByName(name) : ss.getSheets()[0];
  if (!sh) throw new Error('No tab named "' + name + '". Tabs: ' + ss.getSheets().map(s => s.getName()).join(', '));
  return sh;
}

function headers_(sh) {
  const last = Math.max(sh.getLastColumn(), 1);
  return sh.getRange(HEADER_ROW, 1, 1, last).getValues()[0].map(h => String(h).trim());
}

// GET ?secret=...&sheet=...  → connection test; returns tab names and headers.
function doGet(e) {
  const p = e.parameter || {};
  if (p.secret !== SECRET) return respond_({ ok: false, error: 'bad secret' });
  try {
    const sh = sheet_(p.sheet);
    const tabs = SpreadsheetApp.getActiveSpreadsheet().getSheets().map(s => s.getName());
    return respond_({ ok: true, sheet: sh.getName(), tabs: tabs, headers: headers_(sh), rows: sh.getLastRow() });
  } catch (err) {
    return respond_({ ok: false, error: String(err) });
  }
}

// POST {secret, sheet, key_header, key, values: {Header: value}, add_key_column: bool}
function doPost(e) {
  let req;
  try { req = JSON.parse(e.postData.contents); } catch (err) { return respond_({ ok: false, error: 'bad json' }); }
  if (req.secret !== SECRET) return respond_({ ok: false, error: 'bad secret' });
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = sheet_(req.sheet);
    let hdr = headers_(sh);
    let keyCol = hdr.indexOf(req.key_header) + 1;
    if (keyCol === 0) {
      if (!req.add_key_column) return respond_({ ok: false, error: 'key column "' + req.key_header + '" not found' });
      keyCol = hdr.filter(String).length ? sh.getLastColumn() + 1 : 1;
      sh.getRange(HEADER_ROW, keyCol).setValue(req.key_header);
      hdr = headers_(sh);
    }
    // Find the row for this key (e.g. the same Job Posting URL) if it already exists.
    let row = 0;
    const lastRow = sh.getLastRow();
    const n = Math.max(lastRow - HEADER_ROW, 0);
    if (n) {
      const keys = sh.getRange(HEADER_ROW + 1, keyCol, n, 1).getValues();
      for (let i = 0; i < keys.length; i++) {
        if (String(keys[i][0]).trim() === String(req.key).trim()) { row = HEADER_ROW + 1 + i; break; }
      }
    }
    const missing = [];
    const action = row ? 'updated' : 'added';
    if (!row) {
      // New row: the first row whose anchor column (e.g. Position) AND key column are empty.
      // Pre-filled row numbers ("No.") are kept as they are.
      const anchorCol = (hdr.indexOf(req.anchor_header) + 1) || keyCol;
      if (n) {
        const a = sh.getRange(HEADER_ROW + 1, anchorCol, n, 1).getValues();
        const k = sh.getRange(HEADER_ROW + 1, keyCol, n, 1).getValues();
        for (let i = 0; i < n; i++) {
          if (String(a[i][0]).trim() === '' && String(k[i][0]).trim() === '') { row = HEADER_ROW + 1 + i; break; }
        }
      }
      if (!row) row = Math.max(lastRow, HEADER_ROW) + 1;
      sh.getRange(row, keyCol).setValue(req.key);
    }
    const dates = req.date_headers || [];
    Object.keys(req.values || {}).forEach(h => {
      const c = hdr.indexOf(h) + 1;
      let v = req.values[h];
      if (c === 0) { missing.push(h); return; }
      if (v === null || v === undefined || v === '') return;   // never blank out her edits
      if (dates.indexOf(h) >= 0 && /^\d{4}-\d{2}-\d{2}$/.test(v)) {
        const p = v.split('-').map(Number);
        v = new Date(p[0], p[1] - 1, p[2]);                     // a real date, so "d mmm" formatting works
      }
      sh.getRange(row, c).setValue(v);
    });
    return respond_({ ok: true, action: action, row: row, missing_headers: missing });
  } catch (err) {
    return respond_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}
