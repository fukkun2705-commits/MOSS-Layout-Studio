# MOSS Layout Studio

日本語でレイアウトのたたき台を作り、Bounding Boxをブラウザで編集して、**MOSS-Image（ComfyUI）向けのLayout JSON** を出力するWindows用ローカルWebアプリです。

- Python標準ライブラリのみで起動できます（追加のPythonパッケージは不要）。
- **Ollamaは任意**です。接続すると日本語の制作指示から編集可能なレイアウト案を自動生成できます。
- 文字（Text）・物体（Object）・人物／動物（Character）のBoxを移動、拡大・縮小、数値編集できます。
- 編集用JSONの保存と読み込み、ComfyUI用Layout JSONへの変換に対応しています。
- 実写／アニメ調／イラスト調／漫画調の画風選択に対応しています（AI自動設計時）。
- 漫画は各コマを個別のObject Boxとして生成し、タイトルとセリフを独立したText Boxで編集できます。読み順は原則 **右上→左上→次の段** の日本式です。
- 商品カタログ、ポスター、旅行チラシ、集合写真、複数被写体の広告などにも利用できます。

> **重要：** このアプリ自体は画像を生成しません。画像生成には別途、MOSS-Imageを利用できるComfyUI環境が必要です。

## 動作環境

- Windows 10/11
- Python 3.10以上（`py -3` または `python` コマンドで実行できる状態）
- Chrome / EdgeなどのWebブラウザ
- AI自動レイアウト生成を使う場合のみ：Ollamaと利用者が用意する対応モデル（例：`qwen3:4b-instruct`。実際のモデル名はOllamaで確認してください）
- 生成画像を作る場合のみ：ComfyUIとMOSS-Imageの環境（別途導入）

## 起動方法（Windows）

1. 配布ZIPを解凍します。
2. `MOSS_Layout_Studio` フォルダの `start_windows.bat` をダブルクリックします。
3. ブラウザで **http://127.0.0.1:8765/** を開きます（自動で開かない場合は手動で入力）。
4. 終了時は起動したコマンドプロンプトで `Ctrl+C` を押します。

ポートがすでに使用中の場合は、前のMOSS Layout Studioのウィンドウを閉じてから再起動してください。

コマンドラインで起動する場合：

```powershell
python server.py --open-browser
```

## 基本操作

### A. Ollamaを使わず手動で作る

1. 画面上で `＋ 文字`、`＋ 画像・物体`、`＋ 人物` からBoxを追加します。
2. Boxをドラッグして移動、右下のハンドルでサイズ変更します。X/Y/Width/Heightで数値調整もできます。
3. 各Boxの文字列・Descriptionと、画像全体の `Summary Caption` を必要に応じて編集します。
4. `Layout JSONに変換` → `JSONコピー` または `生成用TXT保存` を実行します。
5. ComfyUI側のMOSS-Image T2Iのpositive promptに生成用JSONを貼り付け、StudioとComfyUIの画像解像度を合わせます。

### B. Ollamaでたたき台を作る（任意）

1. Ollamaをインストールし、利用するローカルモデルを自分の環境に用意します。Ollamaのモデルの利用条件はモデルごとに確認してください。
2. Studioの `モデル再読込` からモデルを選択します。
3. 日本語で制作指示を入力し、必要なら画風を選びます。
4. `AIでレイアウト生成` を実行します。
5. **Boxの種類・数・座標・Descriptionを確認し、必要な部分だけ手動修正**します。
6. `Layout JSONに変換` でComfyUI用JSONを取り出します。

短い指示からは完成品ではなく編集できる**たたき台**を作る運用を推奨します。Ollamaの出力はモデルや指示によって変わります。

## 漫画モード

- 漫画では **1コマ＝1 Object Box**、**タイトルと各セリフ＝個別Text Box** を基本とします。
- 読み順は日本式（右から左、上から下）です。5・6・8コマなどで検証していますが、すべてのコマ数での完全再現は保証しません。
- **各コマの場面・行動・必要なセリフを指定する方式**を推奨します。短い筋書きからの自動構成も可能ですが、展開が変わることがあります。
- セリフは正確な表示を保証できません。Boxの移動やDescription修正で調整してください。
- 変形コマの厳密な編集、吹き出しの尻尾や日本語の完全な描画固定は対象外です。

## Descriptionと画風の使い方

Text Descriptionはまず簡潔にすることを推奨します：

```text
Readable Japanese typography displaying exactly "新しい文字".
```

Object/Characterには対象、外観、動作、必要な位置・視点を簡潔に記述します。画風の選択はAI自動生成時の指示に反映されますが、生成画像への反映はMOSS-Imageに依存します。

## JSONの違い

- **編集用JSON**：Studioに再読み込みしてBoxの編集を続けるためのファイル（`編集用JSON保存` / `編集用JSON読込`）。
- **生成用Layout JSON**：`Layout JSONに変換` で生成され、ComfyUIのMOSS-Imageに貼り付ける形式。

この2種類は同じ形式ではありません。`example_draft.json` は**編集用**JSONの例です。

## ComfyUIでの注意

- StudioとComfyUIで幅・高さを揃えてください。
- 生成用Layout JSONをMOSS-Image T2Iノードのpositive promptへ貼り付けます。
- `enable_prompt_enhancer` は**OFF推奨**です（Studioで作った指示を追加変換しないため）。
- 参考の試作条件：4 Steps / CFG 1.0（対応する高速LoRA使用時）。追加検証では25 Steps / CFG 2.5も使用しています。モデル・LoRAとの組合せに応じて適切に設定してください。
- Bounding Boxは生成モデルへの誘導であり、配置・文字・人物の同一性を厳密に保証しません。

## ローカル動作・プライバシー

- Webサーバーは `127.0.0.1:8765` で動作し、LANに公開しません。
- Ollama連携はローカルの `127.0.0.1:11434` を利用します。
- 外部の商用APIキーは必要ありません。
- 外部サービスの利用、MOSS-ImageやOllamaモデルの入手・導入は各利用者が行ってください。

## ファイル構成

```text
MOSS_Layout_Studio/
├─ server.py            # ローカルサーバー／Ollama連携／JSON変換
├─ app.js               # 画面操作
├─ index.html           # ブラウザ画面
├─ start_windows.bat    # Windows起動用
├─ example_draft.json   # 編集用JSONサンプル
├─ README.md            # このファイル
└─ LICENSE              # Apache License, Version 2.0
```

## 公式資料・関係プロジェクト

- [MOSS-Image](https://github.com/OpenMOSS/MOSS-Image)
- [MOSS-Image Layout reference](https://github.com/OpenMOSS/MOSS-Image/blob/main/skills/moss-image-prompt-enhancer/references/layout.md)
- [Ollama](https://ollama.com/)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)

MOSS Layout Studioは独立した非公式ツールです。MOSS-Image、ComfyUI、Ollamaの公式製品ではありません。MOSS-Image本体・各AIモデルはこの配布物に含みません。

## ライセンス

本リポジトリの配布コードは、同梱する **Apache License 2.0** に従って公開する想定です。**公開者は、公開前に著作権の帰属、第三者コード・素材のライセンス適合性を確認してください。** 同梱のLICENSEはApache 2.0の正式なライセンス本文です。
