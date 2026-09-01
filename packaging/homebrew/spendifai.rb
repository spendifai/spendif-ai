# spendifai.rb — Homebrew Cask for Spendif.ai
#
# This file is the TEMPLATE kept in the main repository (spendifai/spendif-ai).
# The published copy lives in the tap repository
#   https://github.com/spendifai/homebrew-spendifai
# under the path:  Casks/spendifai.rb
#
# It is rendered and pushed there by `packaging/homebrew/update-tap.sh`, which
# fills in `version` and `sha256` from a published GitHub Release. Nothing else
# updates it — in particular `packaging/release.sh` does NOT, despite what older
# revisions of docs/release_process.md claimed.
#
# User installation:
#   brew tap spendifai/spendifai
#   brew install --cask spendifai
#
# Since 0.2.1 the DMG is signed with a Developer ID certificate and notarised,
# so the quarantine workaround is no longer needed.
#
# To submit to Homebrew Core (requires ≥75 stars, signed+notarised app,
# stable release history): see docs/release_process.md in the main repo.

cask "spendifai" do
  version "0.2.1"
  sha256 "PLACEHOLDER_SHA256_DMG"

  url "https://github.com/spendifai/spendif-ai/releases/download/v#{version}/SpendifAi-#{version}.dmg"
  name "Spendif.ai"
  desc "Personal finance manager with local AI categorisation"
  homepage "https://github.com/spendifai/spendif-ai"

  # Lets `brew upgrade --cask spendifai` notice a new GitHub Release.
  livecheck do
    url :url
    strategy :github_latest
  end

  # The app runs entirely offline; nothing in it self-updates.
  auto_updates false
  # A bare symbol means "this version or newer"; the ">= :monterey" string form
  # is deprecated since Homebrew 6.
  depends_on macos: :monterey

  # App bundle produced by desktop.spec (BUNDLE name="SpendifAi.app"), renamed
  # on install so Finder shows the product name.
  app "SpendifAi.app", target: "Spendif.ai.app"

  # Post-install: create the data dir the launcher expects for GGUF models
  postflight do
    system "mkdir", "-p", "#{Dir.home}/.spendifai/models"
  end

  # Gracefully quit the app before uninstall (bundle id from desktop.spec)
  uninstall quit: "ai.spendif.desktop"

  # Remove all user data on `brew uninstall --zap`
  zap trash: [
    "~/.spendifai",
    "~/Library/Application Support/Spendif.ai",
    "~/Library/Logs/spendifai-launcher.log",
    "~/Library/Saved Application State/ai.spendif.desktop.savedState",
  ]

  caveats <<~EOS
    Install with

      brew trust --cask spendifai/spendifai/spendifai
      brew install --cask spendifai

    The `brew trust` step is required once per machine: Homebrew 6 refuses to
    load casks from third-party taps until you vouch for them. It is unrelated
    to code signing: since 0.2.1 the DMG is signed with a Developer ID
    certificate and notarised by Apple, so Gatekeeper opens it with no
    quarantine workaround.

    On first launch Spendif.ai downloads a local AI model (2-6 GB depending on
    your hardware) into ~/.spendifai/models. Everything stays on this machine.
  EOS
end
