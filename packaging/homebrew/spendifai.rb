# spendifai.rb — Homebrew Cask for Spendif.ai
#
# This file is the TEMPLATE kept in the main repository (drake69/spendif-ai).
# The published copy lives in the tap repository
#   https://github.com/drake69/homebrew-spendifai
# under the path:  Casks/spendifai.rb
#
# It is rendered and pushed there by `packaging/homebrew/update-tap.sh`, which
# fills in `version` and `sha256` from a published GitHub Release. Nothing else
# updates it — in particular `packaging/release.sh` does NOT, despite what older
# revisions of docs/release_process.md claimed.
#
# User installation:
#   brew tap drake69/spendifai
#   brew install --cask --no-quarantine spendifai
#
# The `--no-quarantine` flag is needed only while the DMG ships unsigned; drop
# it from the instructions once code signing and notarisation are in place.
#
# To submit to Homebrew Core (requires ≥75 stars, signed+notarised app,
# stable release history): see docs/release_process.md in the main repo.

cask "spendifai" do
  version "0.2.0"
  sha256 "PLACEHOLDER_SHA256_DMG"

  url "https://github.com/drake69/spendif-ai/releases/download/v#{version}/SpendifAi-#{version}.dmg"
  name "Spendif.ai"
  desc "Personal finance manager with local AI categorisation"
  homepage "https://github.com/drake69/spendif-ai"

  # Lets `brew upgrade --cask spendifai` notice a new GitHub Release.
  livecheck do
    url :url
    strategy :github_latest
  end

  # The app runs entirely offline; nothing in it self-updates.
  auto_updates false
  depends_on macos: ">= :monterey"

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
    The DMG is not signed or notarised yet, so macOS quarantines it. Install with

      brew install --cask --no-quarantine spendifai

    or, if you already installed it and macOS refuses to open the app:

      xattr -dr com.apple.quarantine "/Applications/Spendif.ai.app"

    On first launch Spendif.ai downloads a local AI model (2-6 GB depending on
    your hardware) into ~/.spendifai/models. Everything stays on this machine.
  EOS
end
