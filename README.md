# rmlabels

Puts shipping labels onto 4-up A4 label sheets (J8169 / L7169, 99.1 × 139 mm):

```
1 | 2
3 | 4
```

It takes PDFs from any carrier or marketplace, images and screenshots, and text-only labels. You can
use it from the command line, from Raycast, or from a small web UI that also works on your phone.

## Install

```sh
uv tool install --force -e ~/code/rmlabels     # puts `rmlabels` in ~/.local/bin
```

Settings go in `~/.config/rmlabels/config.toml`: printer, alignment nudge, text presets and mail.
Every key is optional. The queue, print history and saved position live in `~/.local/share/rmlabels/`.

## Command line

```sh
rmlabels [--start N|auto] [--print|--preview] [--mono] [--draft] [--fill] [--text TEXT ...] FILE ...
rmlabels --grid --print --mono           # alignment test sheet
rmlabels serve                           # web UI on http://127.0.0.1:8765
rmlabels fetch-mail                      # pull label attachments from email into the web queue
```

| Flag | What it does |
|---|---|
| `--start N` | Where the first label goes, `1`–`4` (default `1`) |
| `--start auto` | Starts at the next free spot on your last part-used sheet |
| `--preview` | Opens the result in Preview. This is the default and uses up nothing. |
| `--print` | Prints at 100% (Fine, 720 dpi) and records the job in history |
| `--mono` | Black cartridge only. Raycast and the web UI default to this; the CLI doesn't. |
| `--draft` | 360 dpi. Faster, but barcodes are less sharp. |
| `--fill` | Allows small PDF labels to be enlarged. Images always fill the label. |
| `--text TEXT` | Adds a text-only label (repeatable). Use `\n` for a new line and `@name` for a preset. Text labels go after the files. |

```sh
rmlabels --print --mono a.pdf b.png                  # a PDF and a screenshot
rmlabels --start auto --print --mono --text @fragile label.pdf
rmlabels --text "Please leave with\nnumber 12"      # preview a note
```

**Input**
- **PDFs:** each page counts as one label. Whitespace is trimmed, Click & Drop 2-up and 4-up pages
  are split, and landscape labels are rotated. Parcel2Go's stray `↑` is removed. Pages with only
  text (no barcode or image) are skipped with a warning; use `--text` for notes instead.
- **Images:** PNG, JPG, HEIC, WebP, GIF, TIFF and BMP. White margins are trimmed and the image is
  scaled to fill the label. Crop screenshots first if they include app chrome, because only white
  is trimmed.
- **Our own sheets:** feeding an `rm-labels-*.pdf` back in splits it into its labels again.
- Nothing is ever changed in your original files.

**Output and state**
- Each run saves `~/Downloads/rm-labels-<date>-<time>.pdf`.
- A real print updates the saved next free spot (`next_position`) and adds the job to history.
- Printing a file that has been printed before gives a warning.

## Web UI

The LaunchAgent `com.finjo.rmlabels-web` runs it at http://127.0.0.1:8765.
`tailscale serve --bg --https=8765 http://127.0.0.1:8765` makes it available at
**https://mac.tail9dda7a.ts.net:8765**, from the tailnet only.

- Drop files, paste a screenshot (⌘V) or tap to choose. Add text labels, with preset buttons.
- Pick the start spot on the mini sheet (it defaults to the saved position). The preview shades
  the spots already used.
- Each queued file shows a thumbnail. Badges show *no label found*, *N labels* and
  *printed before* (duplicate).
- **Print** asks for confirmation first. **History** lets you requeue any past labels, or reopen
  the sheet PDF.
- On iPhone: Safari → Share → *Add to Home Screen*.

### iPhone share-sheet Shortcut

1. Create a new Shortcut. In its details, turn on *Show in Share Sheet* and set it to accept
   **PDFs** and **Images**.
2. Add *Get Contents of URL* with:
   - URL: `https://mac.tail9dda7a.ts.net:8765/api/upload`
   - Method: POST
   - Request Body: Form, with a field `file` of type File set to *Shortcut Input*
3. Add *Get Dictionary Value* for `summary` from *Contents of URL*, then *Show Notification*
   with it.

To use it, share a label from Vinted, eBay, Mail or Files and choose the Shortcut. The label
lands in the queue. The Mac has to be awake.

### Email

1. In Proton, make a filter that applies a **Labels** label to label emails from senders such as
   Vinted, eBay and Parcel2Go.
2. In the config, set `[mail] user = "you@proton.me"`. Optionally set `senders`.
3. Store the Bridge password (from the Bridge app, not your Proton password):
   `security add-generic-password -s rmlabels-imap -a you@proton.me -w`
4. Restart the web UI:
   `launchctl kickstart -k gui/$(id -u)/com.finjo.rmlabels-web`

It checks every 5 minutes, and whenever you press *Check now*. PDF and image attachments are
queued, and each email is only looked at once.

## Alignment

If every label comes out slightly off, print `rmlabels --grid --print --mono` on plain paper and
hold it against a label sheet. Then set `nudge_x` and `nudge_y` in the config, in mm: positive
moves right and down.

## Development

```sh
uv run pytest
```

`tests/fixtures/private/` is gitignored and holds real labels, which contain addresses. Tests that
need a fixture that isn't there are skipped. `tests/test_regression.py` checks that the engine
still produces pixel-identical sheets to v1 (`tests/reference/rmlabels_v1.py`).
