import * as vscode from "vscode";
import { execFile } from "child_process";

/** Canonical dict shape produced by promptry's ``SuiteResult.to_dict()`` /
 * ``report.render_json`` and returned by ``promptry run --format json``.
 * See promptry/models.py (SuiteResult.to_dict, TestResult.to_dict). */
interface AssertionResult {
  assertion_type: string;
  passed: boolean;
  score: number | null;
  details: unknown;
}

interface TestResult {
  test_name: string;
  passed: boolean;
  latency_ms: number;
  error: string | null;
  assertions: AssertionResult[];
}

interface SuiteRunResult {
  suite_name: string;
  overall_pass: boolean;
  overall_score: number;
  tests: TestResult[];
}

let outputChannel: vscode.OutputChannel;

function getPythonPath(): string {
  const config = vscode.workspace.getConfiguration("promptry");
  return config.get<string>("pythonPath", "python");
}

function getModule(): string {
  const config = vscode.workspace.getConfiguration("promptry");
  return config.get<string>("module", "evals");
}

function getCwd(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function runInTerminal(name: string, command: string): void {
  const terminal = vscode.window.createTerminal(name);
  terminal.show();
  terminal.sendText(command);
}

function execPromptry(args: string[]): Promise<{ stdout: string; stderr: string; failed: boolean }> {
  const pythonPath = getPythonPath();
  return new Promise((resolve, reject) => {
    execFile(
      pythonPath,
      ["-m", "promptry", ...args],
      { cwd: getCwd(), maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        // promptry exits non-zero both on a genuine CLI/runtime error and on
        // a passing invocation that simply reports a failed suite (see
        // cli.py run_cmd: "raise typer.Exit(1)" on overall_pass=False). In
        // the latter case stdout still holds the machine-readable payload,
        // so callers must inspect stdout before treating this as a reject.
        if (error && stdout.trim().length === 0) {
          reject(new Error(stderr || error.message));
          return;
        }
        resolve({ stdout, stderr, failed: Boolean(error) });
      }
    );
  });
}

async function listSuites(): Promise<string[]> {
  const { stdout } = await execPromptry(["suites", "--module", getModule()]);
  return stdout
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    // suites_cmd prints "  <name> -- <description>" (see cli.py suites_cmd);
    // there is no --format json for this command, so we parse the stable
    // table text and keep only the suite name.
    .map((line) => line.split(" -- ")[0].trim());
}

async function runSuite(): Promise<void> {
  let suites: string[];
  try {
    suites = await listSuites();
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Failed to list suites: ${message}`);
    return;
  }

  if (suites.length === 0) {
    vscode.window.showWarningMessage("No eval suites found.");
    return;
  }

  const selected = await vscode.window.showQuickPick(suites, {
    placeHolder: "Select an eval suite to run",
  });

  if (!selected) {
    return;
  }

  const module = getModule();
  outputChannel.show(true);
  outputChannel.appendLine(`Running suite '${selected}' (module: ${module})...`);

  let stdout: string;
  let stderr: string;
  try {
    ({ stdout, stderr } = await execPromptry(["run", selected, "--module", module, "--format", "json"]));
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    outputChannel.appendLine(`Error: ${message}`);
    vscode.window.showErrorMessage(`promptry run failed: ${message}`);
    return;
  }

  let result: SuiteRunResult;
  try {
    result = JSON.parse(stdout);
  } catch {
    // Not valid JSON: surface whatever the CLI produced instead of
    // silently swallowing it, per the brief ("report rather than hack").
    outputChannel.appendLine(stdout);
    if (stderr) {
      outputChannel.appendLine(stderr);
    }
    vscode.window.showErrorMessage(
      `promptry run '${selected}' produced unparseable output; see the promptry output channel.`
    );
    return;
  }

  for (const test of result.tests) {
    const status = test.passed ? "PASS" : "FAIL";
    outputChannel.appendLine(`  ${status} ${test.test_name} (${test.latency_ms.toFixed(0)}ms)`);
    if (test.error) {
      outputChannel.appendLine(`    ${test.error}`);
    }
    for (const a of test.assertions) {
      const scoreStr = a.score !== null && a.score !== undefined ? ` (${a.score.toFixed(3)})` : "";
      outputChannel.appendLine(`    ${a.assertion_type}${scoreStr} ${a.passed ? "ok" : "FAIL"}`);
    }
  }
  outputChannel.appendLine("");
  outputChannel.appendLine(
    `Overall: ${result.overall_pass ? "PASS" : "FAIL"}  score: ${result.overall_score.toFixed(3)}`
  );

  const scoreLabel = result.overall_score.toFixed(3);
  if (result.overall_pass) {
    vscode.window.showInformationMessage(
      `promptry: '${result.suite_name}' PASSED (score: ${scoreLabel})`
    );
  } else {
    vscode.window.showWarningMessage(
      `promptry: '${result.suite_name}' FAILED (score: ${scoreLabel})`
    );
  }
}

function doctor(): void {
  const pythonPath = getPythonPath();
  runInTerminal("promptry: doctor", `${pythonPath} -m promptry doctor`);
}

function dashboard(): void {
  const pythonPath = getPythonPath();
  runInTerminal("promptry: dashboard", `${pythonPath} -m promptry dashboard`);
}

export function activate(context: vscode.ExtensionContext): void {
  outputChannel = vscode.window.createOutputChannel("promptry");
  context.subscriptions.push(
    outputChannel,
    vscode.commands.registerCommand("promptry.runSuite", runSuite),
    vscode.commands.registerCommand("promptry.doctor", doctor),
    vscode.commands.registerCommand("promptry.dashboard", dashboard)
  );
}

export function deactivate(): void {
  // nothing to clean up
}
