<?php
declare(strict_types=1);

@ini_set('max_execution_time', '600');
@set_time_limit(600);

$projectRoot = __DIR__;
$scriptPath = $projectRoot . '/scripts/cpanel_extract.py';
$uploadDir = $projectRoot . '/tmp_uploads';
$resultDir = $projectRoot . '/tmp_results';

if (!is_dir($uploadDir)) {
    mkdir($uploadDir, 0775, true);
}
if (!is_dir($resultDir)) {
    mkdir($resultDir, 0775, true);
}

$clients = ['Schneider', 'Siemens', 'ABB', 'L And T'];
$selectedClient = '';
$enteredPages = '';
$error = '';
$success = '';
$downloadCsvRelPath = '';
$downloadXlsxRelPath = '';
$rowsCount = 0;
$uniqueCatalogs = 0;
$pagesReturned = 0;
$fastMode = true;
$generateExcel = false;
$textOnlyMode = false;
$resultHeaders = [];
$resultRows = [];
$resultNotes = [];
$debug = [];
$commandTimeoutSeconds = 180;

function parseCommandJson(array $outputLines): ?array
{
    for ($i = count($outputLines) - 1; $i >= 0; $i--) {
        $candidate = trim((string)$outputLines[$i]);
        if ($candidate === '' || $candidate[0] !== '{') {
            continue;
        }
        $decoded = json_decode($candidate, true);
        if (is_array($decoded)) {
            return $decoded;
        }
    }
    return null;
}

function loadCsvPreview(string $csvPath, int $maxRows = 5): array
{
    $headers = [];
    $rows = [];
    $catalogSet = [];
    $pageSet = [];
    $handle = @fopen($csvPath, 'rb');
    if ($handle === false) {
        return [
            'headers' => [],
            'rows' => [],
            'unique_catalogs' => 0,
            'pages_returned' => 0,
        ];
    }
    $line = fgetcsv($handle);
    if (is_array($line)) {
        $headers = $line;
    }
    while (($line = fgetcsv($handle)) !== false) {
        if (count($line) === 1 && trim((string)$line[0]) === '') {
            continue;
        }
        $assoc = [];
        foreach ($headers as $idx => $header) {
            $assoc[(string)$header] = (string)($line[$idx] ?? '');
        }
        if (isset($assoc['catalog_no']) && $assoc['catalog_no'] !== '') {
            $catalogSet[$assoc['catalog_no']] = true;
        }
        if (isset($assoc['page']) && $assoc['page'] !== '') {
            $pageSet[$assoc['page']] = true;
        }
        if (count($rows) < $maxRows) {
            $rows[] = $assoc;
        }
    }
    fclose($handle);
    return [
        'headers' => $headers,
        'rows' => $rows,
        'unique_catalogs' => count($catalogSet),
        'pages_returned' => count($pageSet),
    ];
}

function runCommandWithTimeout(string $cmd, int $timeoutSeconds): array
{
    $output = [];
    $exitCode = 1;
    $timedOut = false;
    $runtimeSeconds = 0.0;
    $error = '';

    if (!function_exists('proc_open')) {
        exec($cmd, $output, $exitCode);
        return [
            'output' => $output,
            'exit_code' => $exitCode,
            'timed_out' => false,
            'runtime_seconds' => $runtimeSeconds,
            'error' => '',
            'mode' => 'exec',
        ];
    }

    $descriptors = [
        0 => ['pipe', 'r'],
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ];
    $process = @proc_open($cmd, $descriptors, $pipes);
    if (!is_resource($process)) {
        return [
            'output' => [],
            'exit_code' => 1,
            'timed_out' => false,
            'runtime_seconds' => 0.0,
            'error' => 'Failed to start Python process (proc_open failed).',
            'mode' => 'proc_open',
        ];
    }

    fclose($pipes[0]);
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $start = microtime(true);
    $stdout = '';
    $stderr = '';

    while (true) {
        $status = proc_get_status($process);
        $stdout .= (string)stream_get_contents($pipes[1]);
        $stderr .= (string)stream_get_contents($pipes[2]);
        if (!$status['running']) {
            $exitCode = (int)$status['exitcode'];
            break;
        }
        $elapsed = microtime(true) - $start;
        if ($elapsed > $timeoutSeconds) {
            $timedOut = true;
            @proc_terminate($process, 9);
            $exitCode = 124;
            break;
        }
        usleep(100000);
    }

    $stdout .= (string)stream_get_contents($pipes[1]);
    $stderr .= (string)stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    proc_close($process);
    $runtimeSeconds = microtime(true) - $start;

    $combined = trim($stdout . "\n" . $stderr);
    if ($combined !== '') {
        $output = preg_split('/\r\n|\r|\n/', $combined) ?: [];
    }
    return [
        'output' => $output,
        'exit_code' => $exitCode,
        'timed_out' => $timedOut,
        'runtime_seconds' => $runtimeSeconds,
        'error' => $error,
        'mode' => 'proc_open',
    ];
}

function detectPythonVersion(string $pythonBin): array
{
    $output = [];
    $exitCode = 1;
    @exec(escapeshellarg($pythonBin) . ' --version 2>&1', $output, $exitCode);
    $raw = trim(implode(' ', $output));
    if ($exitCode !== 0 || $raw === '') {
        return ['raw' => $raw, 'major' => 0, 'minor' => 0];
    }
    if (!preg_match('/Python\s+(\d+)\.(\d+)/i', $raw, $m)) {
        return ['raw' => $raw, 'major' => 0, 'minor' => 0];
    }
    return ['raw' => $raw, 'major' => (int)$m[1], 'minor' => (int)$m[2]];
}

$pythonCandidates = [
    $projectRoot . '/venv/bin/python3',
    $projectRoot . '/venv/bin/python',
    '/usr/bin/python3',
    '/usr/local/bin/python3',
];
$pythonBin = '';
foreach ($pythonCandidates as $candidate) {
    if (is_file($candidate) && is_executable($candidate)) {
        $pythonBin = $candidate;
        break;
    }
}
if ($pythonBin === '') {
    $whichOutput = [];
    $whichExit = 1;
    @exec('command -v python3 2>/dev/null', $whichOutput, $whichExit);
    if ($whichExit === 0 && !empty($whichOutput[0])) {
        $pythonBin = trim((string)$whichOutput[0]);
    }
}
if ($pythonBin === '') {
    $pythonBin = 'python3';
}
$pythonVersion = detectPythonVersion($pythonBin);
$debug[] = 'Python bin: ' . $pythonBin;
$debug[] = 'Python version: ' . (($pythonVersion['raw'] ?? '') !== '' ? (string)$pythonVersion['raw'] : 'unknown');
$debug[] = 'Script exists: ' . (is_file($scriptPath) ? 'yes' : 'no');
$debug[] = 'exec() enabled: ' . (function_exists('exec') ? 'yes' : 'no');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $pages = trim((string)($_POST['pages'] ?? ''));
    $client = trim((string)($_POST['client'] ?? ''));
    // Keep cPanel runtime predictable: always run fast path and skip Excel generation by default.
    $fastMode = true;
    $generateExcel = false;
    $textOnlyMode = false;
    $selectedClient = $client;
    $enteredPages = $pages;

    if (!function_exists('exec') && !function_exists('proc_open')) {
        $error = 'Both exec() and proc_open() are disabled on this hosting account.';
    } elseif (($pythonVersion['major'] ?? 0) < 3 || (($pythonVersion['major'] ?? 0) === 3 && ($pythonVersion['minor'] ?? 0) < 8)) {
        $error = 'Python 3.8+ required. Detected: ' . (($pythonVersion['raw'] ?? '') !== '' ? (string)$pythonVersion['raw'] : $pythonBin) . '. Create/use a virtualenv with Python 3.8+ (recommended 3.10/3.11).';
    } elseif (!is_file($scriptPath)) {
        $error = 'Python script not found at scripts/cpanel_extract.py';
    } elseif ($pages === '') {
        $error = 'Please enter page range (example: 22-30 or 14,16,20-25).';
    } elseif (!in_array($client, $clients, true)) {
        $error = 'Please select a valid client.';
    } elseif (!isset($_FILES['pdf_file']) || ($_FILES['pdf_file']['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK) {
        $error = 'Please upload a PDF file.';
    } else {
        $originalName = (string)$_FILES['pdf_file']['name'];
        $ext = strtolower(pathinfo($originalName, PATHINFO_EXTENSION));
        if ($ext !== 'pdf') {
            $error = 'Only PDF files are allowed.';
        } else {
            $token = bin2hex(random_bytes(8));
            $safePdfPath = $uploadDir . '/upload_' . $token . '.pdf';
            $safeCsvPath = $resultDir . '/result_' . $token . '.csv';
            $safeXlsxPath = $resultDir . '/result_' . $token . '.xlsx';

            if (!move_uploaded_file((string)$_FILES['pdf_file']['tmp_name'], $safePdfPath)) {
                $error = 'Failed to store uploaded PDF.';
            } else {
                $cmdParts = [
                    escapeshellarg($pythonBin),
                    escapeshellarg($scriptPath),
                    '--input ' . escapeshellarg($safePdfPath),
                    '--pages ' . escapeshellarg($pages),
                    '--client ' . escapeshellarg($client),
                    '--output ' . escapeshellarg($safeCsvPath),
                ];
                if ($fastMode) {
                    $cmdParts[] = '--fast';
                }
                if ($textOnlyMode) {
                    $cmdParts[] = '--text-only';
                }
                if ($generateExcel) {
                    $cmdParts[] = '--output-xlsx ' . escapeshellarg($safeXlsxPath);
                }
                $cmd = implode(' ', $cmdParts) . ' 2>&1';

                $commandResult = runCommandWithTimeout($cmd, $commandTimeoutSeconds);
                $output = (array)$commandResult['output'];
                $exitCode = (int)$commandResult['exit_code'];
                $debug[] = 'Command: ' . $cmd;
                $debug[] = 'Runner: ' . (string)$commandResult['mode'];
                $debug[] = 'Exit code: ' . (string)$exitCode;
                $debug[] = 'Runtime (sec): ' . number_format((float)$commandResult['runtime_seconds'], 2);
                $json = parseCommandJson($output);

                if ((bool)$commandResult['timed_out'] === true) {
                    $error = 'Extraction timed out after ' . $commandTimeoutSeconds . ' seconds. Try fewer pages (for example 2-3 pages) and run again.';
                } elseif ((string)$commandResult['error'] !== '') {
                    $error = 'Extraction failed: ' . (string)$commandResult['error'];
                } elseif ($exitCode === 0 && is_array($json) && ($json['ok'] ?? false) === true && is_file($safeCsvPath)) {
                    $preview = loadCsvPreview($safeCsvPath, 5);
                    $rowsCount = (int)($json['rows'] ?? 0);
                    $uniqueCatalogs = (int)($json['unique_catalogs'] ?? $preview['unique_catalogs']);
                    $pagesReturned = (int)($json['pages_returned'] ?? $preview['pages_returned']);
                    $resultHeaders = $preview['headers'];
                    $resultRows = $preview['rows'];
                    $downloadCsvRelPath = 'tmp_results/' . basename($safeCsvPath);
                    if ($generateExcel && is_file($safeXlsxPath) && (bool)($json['xlsx_written'] ?? false) === true) {
                        $downloadXlsxRelPath = 'tmp_results/' . basename($safeXlsxPath);
                    }
                    if ((bool)($json['relaxed_retry_used'] ?? false) === true) {
                        $resultNotes[] = 'No rows in strict keyword mode, retried with relaxed page filter.';
                    }
                    if ((bool)($json['auto_text_fallback_used'] ?? false) === true) {
                        $resultNotes[] = 'No rows in strict structured mode, retried with text fallback.';
                    }
                    if (isset($json['timing_seconds']['total'])) {
                        $timingTotal = (float)$json['timing_seconds']['total'];
                        $timingStruct = (float)($json['timing_seconds']['struct'] ?? 0.0);
                        $timingText = (float)($json['timing_seconds']['text'] ?? 0.0);
                        $resultNotes[] = 'Extractor timing (sec) - total: ' . number_format($timingTotal, 2) .
                            ', struct: ' . number_format($timingStruct, 2) .
                            ', text: ' . number_format($timingText, 2);
                    }
                    if ($rowsCount > 0) {
                        $success = 'Extraction complete.';
                    } else {
                        $success = 'No catalog-price rows found for the selected pages.';
                    }
                } else {
                    $detail = is_array($json) ? (string)($json['error'] ?? 'Unknown error') : implode("\n", $output);
                    $error = 'Extraction failed: ' . $detail;
                }
                if (!empty($output)) {
                    $debug[] = "Command output:\n" . implode("\n", $output);
                }
            }
        }
    }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PDF Catalog Price Extractor</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 0;
      background: radial-gradient(circle at top right, #eef4ff 0%, #f8fbff 35%, #ffffff 75%);
      color: #1f2937;
    }
    .container {
      max-width: 1180px;
      margin: 20px auto;
      padding: 0 20px 24px;
    }
    .app-card {
      border: 1px solid #dbe5ff;
      border-radius: 14px;
      padding: 14px 16px;
      background: rgba(255, 255, 255, 0.92);
      box-shadow: 0 4px 12px rgba(18, 50, 120, 0.06);
      margin-bottom: 12px;
    }
    .app-title {
      margin: 0 0 6px;
      font-size: 28px;
    }
    .app-muted {
      margin: 0;
      color: #4b5f7f;
      font-size: 15px;
    }
    .layout {
      display: grid;
      grid-template-columns: 0.65fr 0.5fr 0.75fr;
      gap: 16px;
      margin-bottom: 12px;
    }
    .panel {
      background: rgba(255, 255, 255, 0.9);
      border: 1px solid #dce3ee;
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 4px 12px rgba(18, 50, 120, 0.04);
    }
    .row {
      margin-bottom: 12px;
    }
    label {
      display: block;
      margin-bottom: 6px;
      font-weight: 600;
    }
    input[type="text"],
    select,
    input[type="file"] {
      width: 100%;
      padding: 10px;
      border: 1px solid #c8d2e0;
      border-radius: 8px;
      box-sizing: border-box;
      background: #fff;
    }
    .btn {
      display: inline-block;
      text-decoration: none;
      border: 0;
      border-radius: 10px;
      padding: 11px 16px;
      cursor: pointer;
      font-weight: 700;
      font-size: 14px;
      text-align: center;
    }
    .btn-primary {
      width: 100%;
      background: #1f6feb;
      color: #fff;
    }
    .btn-secondary {
      background: #f8fbff;
      color: #1f375e;
      border: 1px solid #d9e4ff;
      margin-right: 8px;
      margin-bottom: 8px;
    }
    .error {
      background: #fee2e2;
      color: #991b1b;
      padding: 10px;
      border-radius: 8px;
      margin-bottom: 10px;
    }
    .ok {
      background: #dcfce7;
      color: #166534;
      padding: 10px;
      border-radius: 8px;
      margin-bottom: 10px;
    }
    .info {
      background: #e0f2fe;
      color: #075985;
      padding: 10px;
      border-radius: 8px;
      margin-bottom: 10px;
    }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(140px, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .kpi-item {
      border: 1px solid #d9e4ff;
      border-radius: 10px;
      padding: 10px 12px;
      background: #f8fbff;
    }
    .kpi-label {
      color: #4b5f7f;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .kpi-value {
      font-size: 22px;
      font-weight: 700;
      color: #1f375e;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 8px;
      font-size: 14px;
      background: #fff;
      border: 1px solid #dbe5ff;
      border-radius: 10px;
      overflow: hidden;
    }
    th,
    td {
      border-bottom: 1px solid #edf2fc;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }
    th {
      background: #f8fbff;
      color: #1f375e;
    }
    .hint {
      margin-top: 8px;
      color: #4b5f7f;
      font-size: 13px;
    }
    .meta {
      margin-top: 16px;
      font-size: 12px;
      color: #334155;
      background: #f8fafc;
      border: 1px solid #dbe5ff;
      border-radius: 8px;
      padding: 10px;
      white-space: pre-wrap;
    }
    @media (max-width: 900px) {
      .layout {
        grid-template-columns: 1fr;
      }
      .kpi-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="app-card">
      <h1 class="app-title">PDF Catalog Price Extractor</h1>
      <p class="app-muted">Upload a PDF, set client and pages, then process. Full results are available as CSV or Excel.</p>
    </div>

    <?php if ($error !== ''): ?>
      <div class="error"><?php echo htmlspecialchars($error, ENT_QUOTES, 'UTF-8'); ?></div>
    <?php endif; ?>

    <?php if ($success !== ''): ?>
      <div class="<?php echo ($rowsCount > 0) ? 'ok' : 'info'; ?>">
        <?php echo htmlspecialchars($success, ENT_QUOTES, 'UTF-8'); ?>
      </div>
    <?php endif; ?>

    <?php foreach ($resultNotes as $note): ?>
      <div class="info"><?php echo htmlspecialchars($note, ENT_QUOTES, 'UTF-8'); ?></div>
    <?php endforeach; ?>

    <form method="post" enctype="multipart/form-data" class="layout">
      <div class="panel">
        <div class="row">
          <label for="pdf_file">Upload PDF</label>
          <input id="pdf_file" type="file" name="pdf_file" accept=".pdf" required>
        </div>
        <p class="hint">Supported format: PDF only.</p>
      </div>

      <div class="panel">
        <div class="row" style="margin-bottom:0;">
          <label for="client">PDF Client</label>
          <select id="client" name="client" required>
            <option value="">-- Select PDF client --</option>
            <?php foreach ($clients as $clientName): ?>
              <option value="<?php echo htmlspecialchars($clientName, ENT_QUOTES, 'UTF-8'); ?>" <?php echo ($selectedClient === $clientName) ? 'selected' : ''; ?>>
                <?php echo htmlspecialchars($clientName, ENT_QUOTES, 'UTF-8'); ?>
              </option>
            <?php endforeach; ?>
          </select>
        </div>
      </div>

      <div class="panel">
        <div class="row" style="margin-bottom:0;">
          <label for="pages">Pages</label>
          <input id="pages" type="text" name="pages" placeholder="e.g. 22-30" value="<?php echo htmlspecialchars($enteredPages, ENT_QUOTES, 'UTF-8'); ?>" required>
          <div class="hint">Comma/range: 14,16,20-25</div>
        </div>
      </div>

      <div style="grid-column: 1 / -1;">
        <button class="btn btn-primary" type="submit">Process PDF</button>
      </div>
    </form>

    <?php if ($rowsCount > 0): ?>
      <div class="panel">
        <div class="kpi-grid">
          <div class="kpi-item">
            <div class="kpi-label">Rows</div>
            <div class="kpi-value"><?php echo (int)$rowsCount; ?></div>
          </div>
          <div class="kpi-item">
            <div class="kpi-label">Unique Catalogs</div>
            <div class="kpi-value"><?php echo (int)$uniqueCatalogs; ?></div>
          </div>
          <div class="kpi-item">
            <div class="kpi-label">Pages Returned</div>
            <div class="kpi-value"><?php echo (int)$pagesReturned; ?></div>
          </div>
        </div>

        <div class="row">
          <?php if ($downloadCsvRelPath !== ''): ?>
            <a class="btn btn-secondary" href="<?php echo htmlspecialchars($downloadCsvRelPath, ENT_QUOTES, 'UTF-8'); ?>" download>Download CSV</a>
          <?php endif; ?>
          <?php if ($downloadXlsxRelPath !== ''): ?>
            <a class="btn btn-secondary" href="<?php echo htmlspecialchars($downloadXlsxRelPath, ENT_QUOTES, 'UTF-8'); ?>" download>Download Excel</a>
          <?php endif; ?>
        </div>

        <?php if (!empty($resultHeaders) && !empty($resultRows)): ?>
          <h3 style="margin: 4px 0 0;">Preview (first 5 rows)</h3>
          <p class="hint">Total <?php echo (int)$rowsCount; ?> rows. Download file for complete dataset.</p>
          <table>
            <thead>
              <tr>
                <?php foreach ($resultHeaders as $header): ?>
                  <th><?php echo htmlspecialchars((string)$header, ENT_QUOTES, 'UTF-8'); ?></th>
                <?php endforeach; ?>
              </tr>
            </thead>
            <tbody>
              <?php foreach ($resultRows as $row): ?>
                <tr>
                  <?php foreach ($resultHeaders as $header): ?>
                    <td><?php echo htmlspecialchars((string)($row[(string)$header] ?? ''), ENT_QUOTES, 'UTF-8'); ?></td>
                  <?php endforeach; ?>
                </tr>
              <?php endforeach; ?>
            </tbody>
          </table>
        <?php endif; ?>
      </div>
    <?php endif; ?>

    <?php if ($error !== '' || isset($_GET['debug'])): ?>
      <div class="meta"><?php echo htmlspecialchars(implode("\n", $debug), ENT_QUOTES, 'UTF-8'); ?></div>
    <?php endif; ?>
  </div>
</body>
</html>
