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
# Submitting this cask to the official homebrew/cask repository is not possible
# yet, and the blocker is audience, not software: Homebrew's Package Acceptance
# Policy asks for 30 forks / 30 watchers / 75 stars, tripled to 90 / 90 / 225
# when the author submits their own project. See backlog AI-305.

cask "spendifai" do
  version "0.3.0"
  sha256 "PLACEHOLDER_SHA256_DMG"

  url "https://github.com/spendifai/spendif-ai/releases/download/v#{version}/SpendifAi-#{version}-arm64.dmg"
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
  # The DMG is built by a `macos-latest` runner, which is Apple Silicon, and
  # desktop.spec does not ask PyInstaller for a universal2 binary: the app is
  # arm64-only. Monterey still runs on 2015 Intel Macs, so without this line
  # Homebrew would happily install a binary those machines cannot execute.
  depends_on arch: :arm64

  # App bundle produced by desktop.spec (BUNDLE name="SpendifAi.app"), installed
  # under that same name and NOT renamed.
  #
  # It used to be renamed to Spendif.ai.app for the prettier label in Finder,
  # and that put the same product at two different paths: the DMG is a disk
  # image, so whoever drags it gets SpendifAi.app and no stanza can change
  # that. The two channels then diverged by construction, and brew refused to
  # upgrade a machine where the app had been replaced from the DMG, because it
  # looked for the app at the path only brew ever used. A dot in the label is
  # not worth a class of error that only appears when the channels cross.
  app "SpendifAi.app"

  # Post-install: create the data dir the launcher expects for GGUF models, and
  # record HOW this copy was installed.
  #
  # The marker is the only way the app can tell a cask install from a DMG the
  # user dragged to Applications: both leave an identical bundle in an identical
  # place, and the two need different upgrade instructions. Nothing else knows
  # this, so nothing else can write it. See services/update_service.py.
  postflight do
    system "mkdir", "-p", "#{Dir.home}/.spendifai/models"
    File.write("#{Dir.home}/.spendifai/.install_method", "homebrew\n")
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
