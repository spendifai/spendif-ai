# Changelog

All notable changes to Spendif.ai are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Calendar Versioning](https://calver.org/) as YYYY.M.N from 2026.10.0 (year, month without leading zero, release of the month from 0); earlier releases used Semantic Versioning.

## [Unreleased]

## [2026.10.4] - 2026-10-27

### Added
- Answer by voice in "I have a receipt": the first time, the page asks whether to download the model that listens to you (Whisper small, 190 MB, it stays on your computer); after a yes the conversation goes on hands-free and the reply is read by your system's voice. Italian interface only for now
- Save a receipt whose movement is not imported yet: date, amount, what you bought and the category; when the conversation starts, the receipts that now have their movement are offered one at a time, with your own words next to the movement, and checked only after your yes
- The day said as an ordinal: "il primo", "il secondo del mese", "secondo ottobre"
- The guide and the assistant say the best way to use Spendif.ai and answer "my bank already does this"

### Changed
- One menu for everyone: on top Load your bank statement, Check your movements, I have a receipt, Ask Spendif.ai and Let's take stock (the former Home), where the app opens; every other page in two groups, Insights and Rules and settings. The menu by level and its choice in Settings are gone
- The difference between income and spending in the flow chart is called Holdings, "+" when you put money aside, "-" when you take it
- Only the menu entry of the page on screen is highlighted

## [2026.10.3] - 2026-10-26

### Added
- A first page on an empty registry: be guided from the first step, or go straight to the import
- The menu at your level: on the first day the guided flow, Confirm an expense, the assistant and the Home; the other pages one click away under "More entries". The menu grows with your data and never shrinks by itself, and Settings lets you choose what it shows
- Confirm an expense: shorter replies that never repeat what you just said; "Done." and the next expense is listened to; a short phrase ("that's all") closes with the count of movements checked; "change category" right after a confirmation reopens its category; many more ways to say yes. Answers the words miss are read by the categories model, which only proposes: the app asks back and acts on your yes
- The Registry search finds amounts: 23,40 finds the movements of 23,40 in and out; 23 finds 23,00 to 23,99
- Diagnostics report whether the import categorized and how varied the categories are, as counts only

### Changed
- The conversation of Confirm an expense scrolls in a box of fixed height
- On a 12 GB graphics card the first start downloads the 7B model instead of the 3B; onboarding and download use the same memory figure

### Fixed
- "All dates" in Registry, Analysis and Report covers the whole registry instead of falling back to the current month
- The workflow opened on an empty registry landed on an empty report

## [2026.10.2] - 2026-10-25

### Added
- Confirm an expense: with a receipt in hand, type or dictate its date and amount as people say them ("yesterday, 23 and 40") and the app finds the movement among the outflows within three days. One match is confirmed or its category corrected; several are offered as a choice; none is said plainly. The category comes from your words, and the model is asked only when they name none: three nearest proposals, then the full list. A confirmed movement is marked as checked
- Confirm an expense, voice: replies are read aloud by the operating system's own voice, inside the app window, with nothing sent over the network, and a switch turns it off. Amounts are read as people say them. Answers go with Enter, typed or dictated; with the Mac's dictation only the new words are taken. Known limit: while the Mac's dictation is listening the system holds the voice, so replies are heard with dictation off
- "Always like this?": after three movements of one counterpart given the same category by hand, the Registry asks whether it becomes a rule. Yes writes it and applies it to the movements already there, leaving hand choices and checked movements alone; no is remembered and not asked again
- Every movement remembers who categorized it at import: a rule, the history, the merchant list, the model, a multi-category counterpart, or the fallback category, now told apart from the model. Older movements stay empty rather than guessed

### Changed
- Ticking a counterpart whose category is already right accepts it as it is: its movements keep their category and who gave it, no rule takes over the next imports, and neither the guided steps nor "Always like this?" ask again
- A counterpart with several categories stays on the Rules page, but its rule never runs: its movements keep the category they have, and no broader rule takes them
- "Raw" is now "original description" everywhere, in all five languages

### Fixed
- Rules applied from a page (editing a rule, fixing its movements, applying a new rule at once, similar movements in Review) overwrote categories chosen by hand and checked movements; they now leave them alone, as the import already did, and the counts announced include only the movements that change
- A payment app export with a transaction ID column took the ID as the description, so every movement came in with an empty description and then got the same name and category. A column of all-different codes of one length is never the description, and a new file layout leaving more than 10% of the rows without a description stops for review
- Movements dated in the future keep the proposed category and its source until their date

## [2026.10.1] - 2026-10-24

### Added
- A running import that stops moving says so: after five minutes without progress the panel says how long it has been still and offers to stop it, so a new import can start. Files already completed in that import stay saved
- Right after the onboarding, with nothing loaded yet, the assistant sends you to load a statement first and invents no figures. New FAQ entries answer "I have installed it, what now?"
- The last onboarding step asks whether to go on and load your data: yes opens Let's prepare your movements, no opens the assistant

### Changed
- The category says why, the context says the occasion. Dining out becomes Meals & lodging (Meals, Hotel / B&B); the travel subcategory under leisure goes, since a holiday dinner is a dinner and the holiday is its context; bus, train, plane, ferry and taxi become Public transport & taxi; fuel splits into diesel/petrol/LPG and EV charging. Existing movements, rules and corrections move once; categories you created are not touched
- Movements you checked leave the counterpart grids and their figures, in the counterparts page and in the guided steps

### Fixed
- The local model runs on the graphics card again. Since May the onboarding wrote zero layers on the accelerator, so every installation analysed on the processor, five to eight times slower. New installations now use the card the inference library can address, and at the first start of this version copies already installed are moved back to it, once; a zero you set by hand later is left alone. On Linux and Windows the Vulkan card is now seen before the first model is loaded
- Diagnostics: acceleration is "active" only with a usable card and layers on it; whether a card is available is reported apart
- The log no longer holds the content of your files. The header detection wrote the first rows of every imported file (merchant, amount, date, holder name, IBAN); descriptions, rule patterns, counterpart names and the assistant's questions and answers are gone as well. The log still says how a file was read
- Two amount columns count as debit and credit only when they fill different rows: an amount in the original currency and its fee no longer replace the euro amount. A new file layout that leaves more than 10% of rows without an amount stops for review
- The description no longer joins month, reference numbers, the bank's own category or the exchange rate: a column joins only when it holds mostly letters
- A movement that is not an internal transfer always has a category: a transfer turned back into spending or income takes the model's proposal or the fallback, to review. The fallback for income is the generic subcategory, no longer "sale of used goods". Movements already left without a category are repaired at startup
- A second browser tab, or a reconnection, during an import no longer declares that import interrupted
- Dependencies: gitpython 3.2.1, opentelemetry-api 1.45.1 and filelock 4.2.1 for published vulnerabilities

## [2026.10.0] - 2026-10-22

### Added
- Let's prepare your movements (formerly Workflow): the page every session starts from. Both endings of the onboarding land on it, and the first time it says so, once
- If you want to check: a flow of its own, from its menu entry, to check movements one account at a time. A list shows every account with its last checked movement and the first one still to check; the account furthest behind is proposed, from its oldest unchecked movement. Then a full-page Registry grid with only that account's unchecked movements, oldest first: at every save the checked ones leave and the next ones come in, and a confirmed button marks all those shown as checked. While a rule is being created, saving waits for it; the rule saves the grid's other edits first. When the account is done, its transfers without the other side are asked, then back to the list
- Your own name for a bank movement: renaming one description asks whether to rename every equal movement, only those with the same amount (installments that change) or only this one, and can fix the category as a rule at the same time. The Rules page lists your own names
- Investments and savings: one category in both directions. Spending by asset (property, shares, pension, funds, fixed-term), income by where the money comes from (rent and annuities, coupons and dividends, interest, sales and redemptions). Bank charges get their own category, what is left of finance becomes Insurance, the pension joins employment income, Italian transport gets tolls. Existing movements, rules and budgets move once, in the language of your categories; categories you created stay where they are
- Home Sankey from income to spending: income categories on the left, spending on the right, every column from the biggest branch down. Toggles for spending only, subcategories, and values in percent or euro on branches tall enough for the text. It now comes before the monthly chart
- Every Registry column explains itself on its header, and the totals say which movements they leave out
- One category column in every grid ("Category / Subcategory"), editable grids, and menus that offer only the categories of the movement's direction
- Transfers are paired across imports, movements dated in the future are held until their date, and income has its own workflow step
- The assistant answers from the local model, grounded in the current guides
- Workflow (now Let's prepare your movements): a new entry at the top of the menu takes the user one step at a time, on a clean screen like the onboarding, from what to load through the import, the counterparts and the mixed categories to the period at a glance. Leaving saves the step, and the next visit picks up there, skipping steps already done in the meantime. After the onboarding, "Upload now" opens it at the import step
- Workflow, counterparts step: only the spending counterparts still to check, largest first. One tick per counterpart is enough, and later imports do not ask again. At the top it explains the goal, the counterparts that make 8 euros out of 10 of the period's spending, and says how much has been checked, how much is missing and how many counterparts are enough. The goal is a suggestion: Next always works
- Workflow, mixed categories step: the spending counterparts whose movements sit in more than one category. If the split is a mistake, one category becomes a rule for every movement; if it depends on what was bought, the movements are looked at one by one, since no rule can tell apart movements with the same name. Movements already looked at are never asked again: a counterpart comes back only with what the last import added, and the page says how many were already sorted out. The counterparts step also says how much of the period's spending earlier ticks categorized on their own
- Workflow, import step: when the import is done and nothing waits for an answer, a button at the bottom of the page moves on to the counterparts, so the next step is not missed

### Changed
- The sign of every movement comes from how the account is used and is measured on the data; counterparts are written one way only, and a counterpart has one category
- Every rule has a direction: it holds only for money out or only for money in. Rules that held both ways were split by the movements they covered; 27 movements that an opposite-direction rule had categorized were freed for review
- Home and Registry count the same movements: every movement of the period except internal transfers and card settlements. Card spending now reaches the Home charts
- Registry columns are in reading order: what you read, what you decide, the rule tick, then the symbols that only inform, at half width. Account and type are filters, no longer columns
- Movements you checked keep their category: rules, spreading to similar movements and the model leave them alone. The fallback category is never reused as an example
- Versions follow the calendar from this release: YYYY.M.N
- The Checklist now says what to download next, with the exact dates, above the grid: the account furthest behind comes first with a button to the import, then the other accounts and the empty months in between. With no account yet it asks only for a name; the first time it suggests a single month, so the first round stays short
- Home and Report open on the three months ending with the last transaction loaded, instead of a window ending today. After weeks without importing, the charts no longer open empty: they show where you left off, and a notice says how far you got and points to the Checklist. On Home this is the new default choice, "Last 3 months loaded"; the other periods are unchanged
- The Home period caption no longer says "Last 12 months" whatever period is selected
- On the counterparts page a counterpart can be ticked as checked when its category is already right: the category shown becomes a rule for every movement of the counterpart, past and future. Before, a counterpart became checked only by changing its category
- The Checklist has one name per language, the same in the menu, on the page, in the messages, in the chat and in the guides. French, German and Spanish used the English "Check List" and now read "Liste de contrôle", "Checkliste" and "Lista de control"; the page title names movements with the word the banks use

### Fixed
- Renaming all equal movements also spreads the transfer mark
- Sankey branches no longer spill under the monthly chart
- Dependencies: pillow 13.0.0 and streamlit 1.66.1 for nine pillow CVEs, certifi 2026.10.19 for PYSEC-2026-4320
- The workflow no longer asks again for counterparts that already have one of your rules. The counterparts step used to propose them again, and every import brought them back with the old movements settled and the new ones to settle. "Checked" and "categorized" are now two things: **Category set by you** (how you spent) holds for the counterpart, and a rule decides it for every import; it is what the whole workflow asks. **Checked by you** (you really spent the money) holds for one movement only, is never inherited and is ticked in the Ledger only. Changing a movement's category no longer marks it as checked, and the history learns only from categories you set
- A counterpart you confirmed no longer turns "mixed" after the next import. Confirming a counterpart, or applying a rule to the movements already imported, recorded those movements as changed by hand, while the import records the new ones as categorized by the rule: same category, two sources, and the counterpart looked mixed until the rules were applied again. Movements categorized through a rule are now recorded as such. A counterpart is mixed only when you changed one of its movements by hand, which is a real exception to the rule

## [0.3.5] - 2026-10-12

### Added
- A counterpart can be ruled an internal transfer instead of being given a category. On the counterparts page a tick column marks it, optionally also on the transactions already imported; from then on every import turns its transactions into internal transfers and leaves them out of categorization. Unticking gives them back as expenses or income. The rules page shows these rules and can create or edit one

### Fixed
- The counterparts page speaks plainly: transactions, average amount, who chose the category, checked by you. One label said the opposite of what it measured: the column called variability is the share of transactions with the most used category, and the "low variability" filter actually lists the counterparts whose categories are mixed. It is now called "mixed categories only"
- Counterparts already marked as internal transfers no longer disappear from the counterparts page

## [0.3.4] - 2026-10-12

### Fixed
- The import screen no longer stays on "waiting for schema review" after the review is confirmed. Every import that paused for the review now shows as completed, with the confirmed movements added to its count, and an import that needed no review no longer reports files still waiting from an earlier one

## [0.3.3] - 2026-10-12

### Fixed
- Statements with two date columns written with the month in full ("02 settembre 2026") import with their dates as dates. Both columns used to be taken for text and merged into the description, so every movement carried its dates in the description and the date column held two roles. The column types are now decided by the same date reader the import uses
- The column review no longer saves a column chosen for two roles, such as the same column as amount and as income. It names the column and its roles, and the import waits until the choice is changed

## [0.3.2] - 2026-10-11

### Fixed
- The Chat page works in the installed application. It had been broken in every package ever published, on every system: its module was in none of the packaging lists, so clicking Chat raised an error. A test now fails when a packaged list misses a module the application imports, or names one the repository does not track
- The sign of the amounts is decided by the document, not by the account type chosen in a form. A label set when the account was created could flip a whole month of card expenses into income. Now the amounts are measured and compared with what the model read; when the two agree the import goes ahead, when they disagree the application asks once which way the amounts run, and remembers the answer for that format
- A letterhead drawn with cell borders (logo, period, account holder) is no longer taken for the table of movements. The header detection picked the first bordered rectangle and reported itself certain; it now ignores frames that are mostly empty and picks the fullest table. Images in a spreadsheet do not affect the reading, and a test records it
- A crash leaves its trace in the application log. The only copy used to sit in the launcher log, rewritten at every start, so restarting after a crash erased it. The technical report says whether a crash was recorded, without copying its text, which holds personal paths
- Legacy Excel statements (`.xls`) import in the installed application. The upload accepted them, but the library that reads that format was neither a dependency nor bundled, so every `.xls` stopped with an error. Finding the header row needed a second fix: it scanned the first rows with a reader that cannot open `.xls`, and took the bank's title line as the header
- Dates with the month written out ("02 settembre 2026") are read in Italian, English, French, German and Spanish, full or abbreviated. Before, only English month names parsed, so a statement written that way lost every row
- An import that read almost nothing no longer reports success. A new column mapping that reads fewer than half the rows now stops for review with the reason, instead of being saved and announced as imported with zero movements
- A column mapped to two roles, such as the date column also taken as the description, now sends the mapping to review instead of passing as certain
- The model-based detection of footer rows (totals, balances, notes at the end of a statement) had never run: its prompt raised an error on every call and the statistical fallback always took over. It now runs, with the fallback kept for when the model fails
- Five dependencies moved past their published vulnerabilities: pyjwt, urllib3, fsspec, oauthlib and setuptools
- On macOS both channels install the same bundle, `/Applications/SpendifAi.app`. Homebrew used to install `Spendif.ai.app` and the disk image `SpendifAi.app`, so after replacing one with the other brew refused to upgrade

### Added
- The Debian and Ubuntu package (`.deb`) carries its own Python and the inference library with Vulkan as a plugin, on amd64 and arm64: graphics card acceleration on AMD, Intel and NVIDIA without installing anything else, and nothing compiled on the user's machine. The `.rpm` and Arch packages still use the previous build and get acceleration in a later release
- The import status shows how long it has been running, and on completion how long it took, which is roughly what the next import on the same machine will cost
- support@spendif.ai on the landing and install pages, in every language, for people without a GitHub account. The diagnostics page says where to send its report
- Test reports build the compatibility table on their own. The Diagnostics page opens a GitHub form already filled in with its technical report, and a workflow reads the report, adds the machine to `docs/compatibility.md` (and `docs/compatibility.it.md`) and proposes the change as a pull request. Nobody copies operating system, graphics card or timings by hand. The form is public, so the report is read as untrusted data: only named fields, capped in length, and nothing that can become a link or HTML in the table
- The technical report names the distribution ("Debian GNU/Linux 13 (trixie)") instead of the kernel, and shows the last import on its own next to the average, so a run on the processor and one on the graphics card can be compared. The saved file says what is inside: `spendifai-report_0.3.1_debian-13_arm64_cpu_20261010-1432.xml`
- A test matrix in `docs/compatibility.md` lists the configurations tested before a release and ticks each one from the reports that match it: done when a report says all four steps went through, partly done when some did not. The tick is computed, never typed. `docs/test_builds.md` gives the download and launch commands for every system
- Test reports can also arrive by email, for testers without a GitHub account. The Diagnostics page offers an email whose body is the checklist of the four steps, with the saved document attached, and the saved email goes into the table through the same parser and the same checks as the form
- Windows gets graphics card acceleration through Vulkan, as Linux already does, from one bundle that covers AMD, Intel and NVIDIA. The application also finds the inference library's plugins on Windows: it looked for the loader in a library that, on Windows, does not export it, so it would have loaded none and listed no device

## [0.3.1] - 2026-09-23

### Fixed
- Installing on Linux and Windows no longer compiles anything. llama-cpp-python was built from source on every machine that installed Spendif.ai, which needs CMake and a C/C++ toolchain a desktop does not have: it was the single reason the application could not be installed on a clean Ubuntu or a clean Windows 11. It now arrives as a prebuilt wheel pinned in the lockfile, and first launch is a download instead of three to eight minutes of compiling. macOS is unchanged and still compiles in CI, where the toolchain exists, so GPU inference on Apple Silicon is not quietly lost
- The Debian package declared `gcc` and `cmake` but not `g++` or `make`, while the Fedora package declared all four. The package installed cleanly on Ubuntu and then never started, stopping at `Could not find compiler set in environment variable CXX`
- The Windows installers no longer report success after a failure. A native command that exits non-zero raises nothing in PowerShell 5.1, so the Docker installer announced "Docker trovato" one line below Docker's own message that the engine was not running, then carried on to open a browser on an application that had never started, and the native installer announced that dependencies were installed right after the build of one had failed
- A failed first launch on Linux is visible. The launcher exited before its own error message and dialog could run, so anyone starting the application from the desktop icon saw nothing happen at all. A download that cannot resolve a name is now told apart from a broken installation, since the first case is the network of that machine and not a defect of ours
- The Windows installer accepted whatever Python it found after installing 3.13, so a machine whose PATH still pointed at 3.12 went on using 3.12. The supported range is now checked both before and after, and matches what the project actually declares
- The Linux packages have never carried an application icon. Both builders installed a 256px PNG if it existed, and nothing has ever produced one, so every .deb and .rpm shipped with a desktop entry pointing at an icon name that resolves to nothing

### Added
- Arch Linux is a supported system. The release carries `spendifai-<version>-1-any.pkg.tar.zst`, built and smoke tested in a container that starts with nothing, so a dependency left undeclared fails the build instead of working by accident on machines that happen to have it. Install with `sudo pacman -Syu` followed by `sudo pacman -U ./spendifai-*.pkg.tar.zst`
- Python 3.14 is supported. Rolling distributions have shipped it since August and the Linux environment is built against the system interpreter, so the previous upper bound made the package uninstallable there. The test suite runs on 3.14 in CI
- AMD and Intel graphics cards are named instead of being reported as absent. Acceleration for them is not available yet, which the application now says plainly rather than claiming no GPU is present

### Changed
- Downloading is one click. Every release now also carries each installer under a fixed name, which is what the buttons on the website link, so they resolve to the newest release without a request to the GitHub API: they keep working behind a privacy blocker, past the API rate limit, and with JavaScript turned off
- The compilation of llama-cpp-python for Qwen 3.5 models no longer runs by itself at first launch, where it replaced a known-good wheel with an untested local build. It is available on request with `SPENDIFAI_SSM_BUILD=1`
- Only one way to install on Windows is offered on the website. The source installer needed a toolchain a clean machine does not have and was advertised beside the MSIX

### Security
- The wheels that carry the inference engine are checked against recorded checksums. They come from an index that publishes none of its own, so the lockfile pins their version but not their bytes: the checksums we vetted are committed, and verified on every change to the pin and once a week

## [0.3.0] - 2026-09-22

### Added
- Signed Windows installer. The MSIX carries an Authenticode signature and a timestamp, so Windows installs it instead of refusing an unsigned package. Signing runs on the maintainer's machine against a key held on an HSM, and never in CI (#AI-329)
- In-app update notification for packaged installs. A Homebrew, DMG, .deb or .rpm install now learns when a newer release exists and is shown the upgrade command for the way it was actually installed. Until now the badge was fed only by the source-install launcher's `git fetch`, so a packaged build read a file nobody would ever write (#AI-317)
- `update_event` table recording one row per version this installation has run, with the OS, architecture and install method. Local history that never leaves the machine; Settings > Updates shows it and can turn the check off (#AI-317)
- Signed APT repository. `packaging/linux/update-apt-repo.py` builds and signs the archive published at `spendifai/apt`, so Debian and Ubuntu get `apt update` and `apt upgrade` instead of a one-shot `apt install ./file.deb` (#AI-322)
- The Homebrew cask is now published automatically when a release is published, rather than by a manual step that could be forgotten (#AI-316)

### Fixed
- The Windows app installed and then closed on launch, showing only the startup screen. The bundle was symbol stripped, which is not supported on Windows and left the Python runtime unloadable, so the app died before running a line of its own code
- The macOS install script accepted Python 3.11, which the project cannot use, and failed several steps later inside the dependency resolver. It now checks both ends of the supported range before doing anything, and a failure caused by the interpreter is no longer reported as a Metal build problem

### Changed
- macOS install instructions on the website now use the Homebrew tap, which also keeps the app updated, instead of the one-shot install script

### Security
- The `.deb` postinst and `.rpm` `%post` no longer pipe `curl` into a shell as root to install uv. The version is pinned, the asset is downloaded by name, and its sha256 is verified before anything executes. On mismatch the step refuses with no fallback (#AI-321)
- `anyio` upgraded to 4.14.2 for CVE-2026-63374 and CVE-2026-64847

### Fixed
- Linux packages shipped whatever version happened to be committed in `core/_build_info.py`, because only the macOS and Windows builders stamped it. With the new update check that stale value would have meant a permanent false "update available" for every Linux user (#AI-320)
- The `.deb` declared `Section: finance`, which is not a Debian section, depended on the obsolete `pkg-config` instead of `pkgconf`, and shipped neither a changelog nor a copyright file. lintian now runs in CI (#AI-321)

## [0.2.1] - 2026-09-01

### Changed
- The macOS build is signed with a Developer ID certificate and notarised by Apple. Gatekeeper now accepts both the DMG and the app with no workaround: no `--no-quarantine`, no `xattr -dr` (#AI-243)
- Homebrew cask install instructions no longer require `--no-quarantine`

### Fixed
- `packaging/macos/sign-local.sh` signed the app bundle with `codesign --deep`, which Apple discourages and which leaves nested Mach-O binaries without hardened runtime. It now signs inside-out, applies the entitlements a frozen Python bundle needs, and warns when the DMG is built before the app is signed


## [0.2.0] - 2026-08-25

### Added
- Traffic-light privacy bar on the LLM configuration page: shows at a glance what leaves the machine for each backend, before the user picks one (#AI-229, #161)
- Landing pages: SEO + GEO metadata, `sitemap.xml`, `robots.txt` and `llms.txt` (#155)
- Alpha feedback page on gh-pages, backed by a Tally form (#162)
- Direct installer download buttons (DMG / MSIX / .deb / .rpm) on the getting-started page, in all 9 locales (#163)
- New brand mark applied to the landing pages and to the app bundle icons (#159)

### Changed
- Sidebar navigation fully translated to Italian (#158)
- Documentation: Python >= 3.12 stated in the install instructions, English brand landing, reciprocal IT README link (#164)
- Version references aligned across README and `pyproject.toml` (#160)

### Fixed
- Desktop window: the Streamlit Deploy button and its toolbar are no longer visible inside the pywebview window; `toolbarMode = viewer` keeps Settings and Theme reachable (#165)
- Coupling: the UI layer no longer imports `db`/`core` directly, and the coupling CI gate that was silently passing is repaired (#157)

### Security
- Dependency floors raised after a pip-audit finding: `gitpython >= 3.1.58` (GHSA-9rj7-rf2p-w77r, GHSA-4gmw-gg2m-w46p, CVE-2026-76217, GHSA-wvpp-8hx9-p66j, GHSA-jm78-9fvv-mhgr) and `cryptography >= 50.0.0` (PYSEC-2026-3552). Lockfile resolves to gitpython 3.1.59 and cryptography 50.0.0
- Docker image slimmed down and hardened: the packaging toolchain of the system interpreter (pip, setuptools, `pkg_resources`, bundled wheels) is removed once the build is done, and `uv sync` no longer leaves its build cache in `/root/.cache/uv`. Neither is reachable from the app — uv installs everything into `/app/.venv` and the entrypoint runs through `uv run`. Note: this did **not** clear the two HIGH findings Trivy reports on the image (setuptools 70.3.0 / CVE-2025-47273 and msgpack 1.1.2 / GHSA-6v7p-g79w-8964); their location in the image is still unknown and the container security gate stays red for this release

### Known limitations
- The macOS DMG and the Windows MSIX are **not signed**: Gatekeeper and SmartScreen will warn on first launch. Code signing and notarisation are tracked separately
- The winget manifests and the Homebrew cask in `packaging/` still reference the retired Windows ZIP artefact and a `Spendif.ai-<version>.dmg` filename; neither channel is submitted for this release

## [0.1.0] - 2026-07-29

*First public release, tagged and published on 2026-07-29 with installers for macOS, Windows and Linux. The entries below were previously split between an `Unreleased` section and a `0.1.0` section dated with the development start (2026-04-06); they are merged here to match what actually shipped in the v0.1.0 artefacts.*

### Added
- Initial release
- Import CSV/XLSX bank statements (9 Italian financial instruments)
- Local AI categorisation via llama.cpp (Qwen3.5, Gemma4, Phi4, Llama3.2)
- NSI/OSI-aware counterpart matching
- History cache, user rules, taxonomy customisation
- macOS .app bundle, Spotlight integration
- Windows installer via winget/PowerShell
- Interactive analytics dashboard (coming soon)
- Native desktop app: pywebview window embedding Streamlit (no Terminal, no browser)
- Auto-download of recommended LLM model based on hardware detection (RAM/GPU)
- Zero-config llama.cpp setup on first launch
- Splash screen with download progress bar
- macOS installer (`packaging/macos/install.sh`) with native window launcher
- Windows installer (`packaging/windows/install.ps1`) with native window launcher
- Ubuntu/Debian installer (`packaging/linux/install-debian.sh`) and .deb builder
- Red Hat/Fedora installer (`packaging/linux/install-redhat.sh`) and .rpm builder
- PyInstaller spec file for building standalone .app (macOS) and .exe (Windows)
- Uninstall scripts for macOS, Windows (with `-Silent` flag), and Linux
- Windows Add/Remove Programs registration during install
- Linux CI workflow (`release-linux.yml`): builds .deb/.rpm, smoke-tests in containers, attaches to GitHub Release
- winget identifier renamed to `SpendifAi.SpendifAi`
- Windows MSIX installer (`packaging/windows/build-msix.ps1` + `AppxManifest.xml.in`), replaces the previous ZIP-only Windows artefact
- Local signing scripts: `packaging/macos/sign-local.sh` (codesign + notarytool + stapler) and `packaging/windows/sign-local.ps1` (SignTool wrapper)
- Local DMG builder (`packaging/macos/build-dmg.sh`) mirroring the CI job for offline reproducibility
- Hybrid CI release model: `release.yml` builds all four installers unsigned and publishes a **draft** GitHub Release; the owner signs DMG and MSIX locally and replaces them via `gh release upload --clobber` before flipping `--draft=false`. Documented in `docs/release_process.md` §2bis (EN+IT)
- Getting-started page on gh-pages, full 9-locale coverage (`getting-started.{html,en,de,es,fr,ja,nl,pl,pt}.html`): three-step illustrated install/first-launch guide with download buttons for DMG/MSIX/.deb/.rpm and screenshot placeholders (`assets/screenshots/`). Each page includes the Cloudflare Web Analytics beacon and a full language switcher
- Updated `installation_{macos,windows}.{md,it.md}` "First Launch" section to describe the native pywebview flow (splash + model download + onboarding wizard), replacing the obsolete Terminal/browser sequence which only applies to the legacy `install.sh`/`install.ps1` scripts
- Landing pages (all 9 locales: `index.html` IT, `index.{en,de,es,fr,ja,nl,pl,pt}.html`): added localized "Download installer" CTA pointing to the locale-matched getting-started page above the existing curl-script tabs
- README restructured for technical/developer audience (was a mix of end-user + dev content): trimmed from 663 → ~180 lines, banner directing end users to the gh-pages getting-started, six "What's implemented" bullets with file paths + RF-codes + honest beta tags, develop-locally section with Ollama vs llama.cpp clarification, reciprocal IT·EN documentation links
- New `docs/architecture.{md,it.md}` capturing the layer diagram and Flow 1 vs Flow 2 details previously inlined in the README
- New `docs/design_decisions.{md,it.md}` capturing the Decimal/SHA-256/invert_sign/RF-03/RF-04 rationale previously inlined in the README, with explicit `(beta)` status flags on RF-03 and RF-04
- New "What you get" section on all 9 `getting-started.*.html` pages with six user-value bullets (translated into all locales) — duplicate-free framing complementing the README's "What's implemented"
- README "What leaves the machine" section made explicit and honest: shows a concrete before/after PII redaction example and acknowledges that transaction **amounts** and **dates** still travel to remote LLM backends in the current implementation (backlog AI-55 covers redaction modes). Privacy bullet on all 9 gh-pages getting-started rewritten in the same honest tone
- Persistent launcher boot log at `~/Library/Logs/spendifai-launcher.log` (macOS) / `~/.spendifai/spendifai-launcher.log` (Linux/Windows). Captures stdout/stderr from the bundled launcher even when `console=False` in `desktop.spec` would otherwise route them to `/dev/null`. Truncated on every launch
- Parallel model download with live ETA banner. `desktop/launcher.py` now splits the synchronous environment bootstrap from the multi-GB model download. The download runs on a daemon thread and updates `~/.spendifai/model_download.status` with `pct`, `elapsed_s`, `eta_remaining_s`. A new `ui/widgets/model_download_banner.py` reads it via `st.fragment(run_every=2)` and shows a sticky banner without rerunning the whole page. `core/model_manager._download_from_hf` wires Hugging Face's tqdm into our callback for real per-chunk progress (previously the callback only fired at 100%)
- Immersive first-run page. When `onboarding_done == false`, `app.py` injects CSS to hide sidebar / header / toolbar and renders only the download banner + onboarding wizard. As soon as the wizard is confirmed the layout reverts to the normal sidebar app
- Cross-arch `.deb` build. `release.yml` runs `build-deb.sh` for both `amd64` and `arm64` via a matrix; same Python source, only the `Architecture:` declaration in `DEBIAN/control` changes (postinst compiles native deps on the target machine via `uv sync`). Unblocks install on Apple Silicon VMs (#AI-57)
- Documented end-to-end developer flow in `docs/desktop_build_and_test.{md,it.md}`: local DMG build, CI MSIX via `workflow_dispatch`, MSIX install on a clean VM through `packaging/windows/dev-install.ps1` (auto self-signed cert, signs, installs), all log paths, total cleanup
- `packaging/windows/dev-install.ps1`: one-shot script for testers — generates a self-signed cert with the manifest's exact Publisher CN, imports it as trusted, locates SignTool, signs the MSIX, and `Add-AppxPackage` to install. Idempotent (cert reuse), auto-elevates if not Administrator
- Total cleanup scripts for all three OSes: `packaging/macos/cleanup.sh`, `packaging/linux/cleanup.sh`, `packaging/windows/cleanup.ps1`. Each kills running processes, uninstalls the system package (AppX / apt / dnf / .app removal), wipes `~/.spendifai`, the Hugging Face model cache, and launcher logs. Options for `--keep-models` to preserve the GGUF cache and (Windows-only) `-RemoveDevCert` to also drop the self-signed cert

### Fixed
- Model registry no longer points the 16 GB tier at the gated `google/gemma-3-12b-it-GGUF` repo (anonymous downloads got HTTP 401 → entire AI feature disabled on Apple Silicon / NVIDIA workstations). Now uses the public `bartowski/google_gemma-3-12b-it-GGUF` re-up with the same model weights. `ensure_model_available` also gained an automatic fallback chain: on any download failure it tries the next-smaller tier instead of returning `None`. Worst case a 64 GB host falls back gemma-3-12b → qwen2.5-7b → qwen2.5-3b → qwen2.5-1.5b. Closes #AI-59
- Onboarding wizard now gates the final "Start" button on the LLM model download. The desktop launcher kicks off the download as soon as the app boots; the user can navigate all wizard steps in parallel while the model streams in the background. On the summary step the button stays disabled with a live "⏳ Waiting for AI model — 78% · ~3 min" indicator until the download reaches 100%. Auto-refreshes every 2 s without losing wizard state (#AI-58 follow-up)
- Onboarding wizard now seeds invisible LLM defaults during its apply step (`llm_backend=local_llama_cpp`, `cat_llm_backend=local_llama_cpp`, `llama_cpp_n_gpu_layers=0`, `llama_cpp_n_ctx=4096`, `llama_cpp_model_path` from `$LLAMA_CPP_MODEL_PATH` set by the launcher). Without this a fresh user who completed the wizard would still hit `llm_backend not configured` on the Import page
- Auto-skip migration tightened to require ALL four onboarding-wizard prerequisites before silently flagging an existing user as already-onboarded: `ui_language` set, `owner_names` non-empty, `llm_backend` configured, and ≥1 row in `account`. Previously a single populated taxonomy row was enough — but the default taxonomy ships seeded on every fresh install, so every brand-new user was being skipped past the wizard (#AI-58)
- Schema auto-invalidation: cached schemas (Flow 1) producing < 10% parse rate are automatically deleted and retried with Flow 2 (LLM re-classification)
- Orphan schema purge: startup migration removes `document_schema` rows without `header_sha256`, preventing unreachable stale entries
- Header SHA256 always populated on schema before persist, preventing orphan schemas from being created
- Sanitizer crash on pandas 2.x `string`-dtype columns with NaN: `astype(str).tolist()` was leaking float NaNs into the regex-based sanitizer, raising `TypeError: expected string or bytes-like object, got 'float'` on real bank CSV imports. Fix in `core/classifier.py` (`fillna("").astype(str)`) plus defensive guard in `core/sanitizer.py` (`redact_pii` coerces non-string inputs to `""`). 5 regression tests in `tests/test_sanitizer.py::TestNonStringInputs` (#108)
- Desktop bundle silent crash chain: five distinct bugs that made the PyInstaller `.app` exit with zero output on every launch — `console=False` swallowing stdout, `splash.html` path collapsing to `_MEIPASS` instead of `_MEIPASS/desktop/`, infinite re-exec loop when launcher.py invoked `subprocess.Popen([sys.executable, "-m", "streamlit", …])` (the executable is the bundle, which re-entered the launcher), Streamlit refusing `--server.port` because `global.developmentMode` defaulted to `true` in the bundle, and `support/logging.py` hard-coding `os.makedirs("logs")` which is read-only inside the bundle. All addressed in `fix(desktop): make the PyInstaller bundle actually start` (#AI-49 follow-up)
- Subprocess tree not killed on window close. `_cleanup` now uses `os.killpg` against process groups started with `start_new_session=True` so Streamlit + uvicorn + grandchildren die together. Previously the leftover Streamlit kept the port open and macOS Launch Services would treat the next double-click as "reopen of the running instance"
- `packaging/linux/build-rpm.sh` icon entry in `%files` made conditional on `ICON_PRESENT=1` so rpmbuild succeeds even when no `spendifai_256.png` is present on the build host (closes AI-56)
