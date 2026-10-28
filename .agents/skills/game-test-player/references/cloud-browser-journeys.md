# Cloud向けブラウザー経路検証

## 調査と選択（2026-10-28 JST）

GitHubの既定ブランチは `master`、調査時の先端は `905a202732e6dbb2d6745ec7d0f79cab435ccca1`。`main` は存在しない。既存の `design.md`、`runner-protocol.md`、`session-schema.json`、`SKILL.md` と、PR #1側の `AGENTS.md`、`MGOP_SPEC.md`、PR #2の全差分を確認した。

| 対象 | 調査結果 | 今回の扱い |
| --- | --- | --- |
| master | Windows Godotウィンドウ入力・撮影、汎用Python SessionRecorder、初見方針がある。Cloud向け経路実行はない | Recorderと既存の入力操作名を再利用 |
| [Draft #1](https://github.com/madowaku/game-test-player/pull/1) `33cffae8` | MGOP、instrumented runner、SPACEスモーク、EvidenceBundle | 変更ファイルに触れず、依存もしない |
| [Draft #2](https://github.com/madowaku/game-test-player/pull/2) `4688edc1` | #1に積み上げたGodot headless通信検証とpause中のbridge処理修正。scenario経由の状態変化で、通常入力や画像は検証しない | Godot bridge・fixture・self_check・CIの変更を重複させない |
| Godot headless | `--headless` はheadless displayとDummy audio。スクリプト・状態・通信の検証に適する。`Input.action_press` は `_input()` を呼ばず、`parse_input_event` はOS入力の証明にならない | 今回は調査のみ。PR #2の実行結果を本PRの実行証拠に流用しない |
| ブラウザー | Playwrightのキーボードdown/up、マウス座標入力、headless Chromiumのスクリーンショットを組み合わせられる | Cloudで画面付き経路証跡を得る改善として選択 |

参考一次資料（上記日付に確認）：[Godotコマンドライン](https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html)、[Godot Input](https://docs.godotengine.org/en/stable/classes/class_input.html)、[Playwrightキーボード](https://playwright.dev/python/docs/api/class-keyboard)、[スクリーンショット](https://playwright.dev/python/docs/screenshots)、[ブラウザーとLinux依存の導入](https://playwright.dev/python/docs/browsers)。

選んだ改善は **明示的なブラウザー回帰経路の実行と画面証跡による判定**。Godot通信層の拡張よりも、現在不足する通常入力からの開始・失敗・成功・リトライと描画の検証を補えると判断した。既存の初見runnerには回帰マクロを混ぜない。追加はbrowser adapter、独立CLI、専用fixture・検証・文書・CIのみ。

## Codex Cloud / Linuxで実行

Python 3.12を使用。セットアップ時にPyPIとPlaywrightのダウンロード先へ接続できる必要がある。Linuxのブラウザー共有ライブラリもセットアップ時に導入する。導入後の同梱fixture試験はloopback内だけで完結し、デスクトップ、GPU、実機、外部サービス、APIキーを必要としない。

```bash
python -m pip install -r .agents/skills/game-test-player/requirements-browser.txt
python -m playwright install --with-deps chromium --only-shell
python .agents/skills/game-test-player/scripts/self_check.py
python .agents/skills/game-test-player/scripts/self_check_browser.py --out evidence/browser-run-001
```

`self_check_browser.py` は一時的なloopback HTTPサーバーを空きポートで起動・終了し、正常系と8種類の欠陥注入を実ブラウザーで走らせる。**欠陥ごとのexit 1と検出位置を検証**するため、「別の環境エラーで落ちただけ」を検出成功とは数えない。全受入条件を満たした場合のsuite全体の終了コードは0。欠陥を含む個々のsessionはfailureのまま保存する。

既存の出力先は上書きしない。次回は `browser-run-002` 等を選ぶ。Chromiumの子プロセスを許可しない環境では実行できない。セットアップ・実行失敗をゲーム品質の合格に読み替えない。GitHub Actionsの `browser-journey-ci.yml` でも同じLinuxコマンドを実行し、成否にかかわらず証跡を7日保持する。Cloud向け手順とUbuntu CIは、Codex Cloudの実行済み証明とは区別する。

## 実際のゲームに適用

ゲームを作業環境内で配信し、fixtureの `journey.json` をコピーして表示・座標・期待画面をそのゲームに合わせる。プロセスを外部公開する必要はない。

```bash
python .agents/skills/game-test-player/scripts/run_browser_journey.py \
  --url http://127.0.0.1:8000/ \
  --journey path/to/journey.json \
  --out evidence/my-game-001 --timeout 3 --max-seconds 90
```

マニフェストには名前、固定viewport、stepsを記述する。step 0は観測のみ、以降は通常操作が必須。空経路・無検証step・未知フィールド・非有限待機・評価用JavaScript操作は拒否する。`phase` は作者のラベルであり、到達したという独立した証明ではない。各ゲームの期待値を正しく設計する責任は経路作者にある。

```json
{
  "name": "move-right",
  "phase": "input",
  "action": {"action": "press_key", "key": "ArrowRight", "hold_s": 0.08},
  "expect": {
    "text": [{"selector": "#status", "equals": "Playing"}],
    "pixels": [{"selector": "#board", "x": 140, "y": 40, "width": 20,
                "height": 20, "rgb": [82, 211, 153], "tolerance": 8, "min_ratio": 0.95}]
  }
}
```

操作は `press_key`、`key_down`、`key_up`、`mouse_click`、`wait`。キーはPlaywright表記。pressはdown→待機→upで、例外時もreleaseを試みる。mouseはviewport内のCSSピクセル座標。通常のプレイ中はゲーム関数、MGOP、内部変数、結果を強制するAPIを呼ばない。HUDは表示テキストだけを読み、PNGの領域内で期待RGBに収まるピクセル率を計算する。領域は指定要素の左上からのCSS座標で、DPR=1。描画内容がDOMで表現されないCanvasにも使える。

各操作後、期待値が揃うか `--timeout` に達するまで再観測する。`--max-seconds` は経路全体の予算で、実行中の撮影/テキスト読取は操作単位のtimeoutまで続きうる。OSやブラウザードライバー自体のハングに対する厳密なwatchdogではない。CIには別途10分のジョブ上限を設定している。マッチした後の安定性を確認するには、fixtureの `released-idle` のようなwait＋同じ期待値のstepを追加する。

終了コードは **0=全チェック合格、1=画面の不一致またはruntime error、2=入力設定・実行基盤・証跡の不備**。コード1のSession outcomeはfailure、2はaborted。経路中のゲーム内Failed画面は期待できる正常な検証対象であり、検証全体のfailureとは別。

## 証跡と再現

各実行は `persona=regression`、`mode=diagnostic`、`black_box=false` と明記する。既知経路・ソース既知fixture・ブラウザーエラーチャネルを使うため、初見プレイヤーを名乗らない。

| ファイル | 内容 |
| --- | --- |
| `session.json`, `report.md` | 既存Recorder形式、実行した入力、到達点、失敗の再現手順・severity、空のままの主観評価項目 |
| `journey.json` | 実行した期待値と操作のコピー |
| `browser-run.json` | URL、経路SHA256、OS/Python/Playwright/Pillow/Chromium、viewport、予算、step一覧、終了コード |
| `screenshots/step-NNN.png` | 初期画面と各操作後の最後の画面。失敗画面も保存 |
| `step-NNN.json` | 同じstepの文字列実測、領域色一致率、期待値、入力/撮影/runtimeエラー |
| `browser-errors.json`, `trace.zip` | エラーチャネルとPlaywright trace。起動に失敗した場合はtraceが作れない場合がある |
| suiteの `acceptance.json` | 各欠陥の検出位置・操作数。合否の正本はsuiteの終了コード |

レビュー時は同じstep番号のPNG・JSONと直前の操作を突き合わせる。traceは `python -m playwright show-trace <trace.zip>` で必要に応じて開く。session内のスクリーンショット参照は既存仕様どおり実行環境の絶対パスなので、CI artifactをダウンロードした後は同じstep名のローカル `screenshots/` を参照する。

## 実測した検出事例

2026-10-28 JST、Windows上のPython 3.12.14 / Playwright 1.64.1 / Pillow 13.0.0 / headless Chromium 156.0.8078.4で実行。正常系は **16操作・17観測、findingsなし**。開始→右→赤タイルで失敗→終了中入力抑止→Retry→下の迂回路→金タイルで成功→終了中入力抑止→Retry→再操作→キーrelease後の待機を通過した。

以下は**検出器を検証するために作ったfixtureの欠陥**であり、既存ゲームで発見した不具合とは主張しない。すべて同じ経路を通常入力で再生し、終了コード1と指定位置での停止を確認した。

| 注入欠陥 `--case` | 再現する入力 | 検出step / 観測 |
| --- | --- | --- |
| `start-ignored` | 起動→Startクリック | 1 `start`：Readyのまま |
| `input-ignored` | Start→右 | 2 `move-right`：位置も画像も動かない |
| `failure-ignored` | Start→右→右 | 3 `hit-hazard`：赤タイル上でもPlaying |
| `retry-broken` | 赤タイルで失敗→Retry | 5 `retry-failure`：Failedと位置が残る |
| `success-ignored` | 失敗後Retry→下→右×4→上 | 12 `reach-goal`：金タイル上でもPlaying |
| `success-retry-broken` | 成功→Retry | 14 `retry-success`：Successのまま |
| `stale-render` | Start→右 | 2 `move-right`：HUDは `Position: 1,0`、緑のプレイヤーは元の位置。4つの文字列チェックは合格、画像の一致率は0% |
| `runtime-error` | Start→右 | 2 `move-right`：入力ハンドラーの未捕捉例外を保存 |

単独再現：

```bash
python .agents/skills/game-test-player/scripts/self_check_browser.py \
  --case stale-render --out evidence/repro-stale-001
```

このコマンドは「欠陥が期待どおり検出された」場合に0で終わる。`stale-render/session.json` と `step-002.json` はfailureのまま。画面にもHUDと描画の食い違いが残る。修正相当の比較は `--case none` を別の出力先で実行し、同じstep-002と比較する。

追加でHTTP 404、短い実行予算、出力先の再使用拒否、空経路、チェックなし、JavaScript注入操作、NaN待機、誤記フィールドを検証した。既存 `self_check.py` も変更せず通過。Godot実機・MGOP・人間による楽しさは今回実行していない。

## 偽陽性・偽陰性と適用範囲

- **偽陽性**：正当なロード時間がtimeoutを超える、座標/文言/配色の意図した変更、ブラウザー/フォント/色管理の差、許容してよいconsole errorでも停止する。viewport・依存を記録し、まず画面とtraceで確認する。全画面の完全一致は要求せず、小さな安定領域に許容色差を設けた。
- **偽陰性**：指定していない経路・座標・音声・ネットワーク・長時間プレイの問題、領域内にたまたま同色の別物がある、誤った期待値、期待状態を一瞬だけ通過して後で壊れる場合は漏れる。画像全体の美しさ・意味を評価するものではない。見た目が変化しないこと自体をバグ扱いせず、待機やメニューでの誤検出を避ける。
- DOMのvisible判定はピクセルとして読めることを保証しない（被覆・画面外など）。PNGも併用するが、全テキストのOCR・被覆判定は行わない。スクリーンショットとDOM取得は厳密な同時刻ではなく、高速な状態変化ではずれる。安定チェックポイントを選ぶ。
- ブラウザー入力はブラウザーイベント経路の検証であり、物理キーボード・ゲームパッド・タッチ・OSフォーカス・実GPU・ネイティブGodotの検証ではない。このfixtureは離散移動で、連続物理やフレームタイミングは未検証。
- 正常1ケースと人工欠陥8ケースの合否から、未知の実ゲームに対する検出率や偽陽性率は推定できない。実ゲームへの適用にはそのゲームの経路と観測契約が必要。
- Linux CI/Cloudで実行できる構成と、特定のCodex Cloudジョブで実行済みであることは別。実行環境と結果は各 `browser-run.json` およびPRのCIリンクで確認する。
- 楽しさ、初見理解、操作感、アートの最終判断、実機品質の保証を自動合格から導かない。既存のGodot / 初見セッションと併用する。
