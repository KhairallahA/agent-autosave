# Homebrew formula for agent-autosave. The tap repository
# (KhairallahA/homebrew-agent-autosave) carries a copy of this file;
# scripts/update-formula.py sets url and sha256 from the published sdist.
class AgentAutosave < Formula
  include Language::Python::Virtualenv

  desc "Autosave for coding agents: hand a task from one agent to the next"
  homepage "https://github.com/KhairallahA/agent-autosave"
  url "https://files.pythonhosted.org/packages/00/00/000000000000000000000000000000000000000000000000000000000000/agent_autosave-0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "Apache-2.0"

  depends_on "python@3.13"

  def install
    # Standard library only: no resources to vendor.
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/agent-autosave --version")
    ENV["HOME"] = testpath
    (testpath/"repo").mkpath
    cd testpath/"repo" do
      system "git", "init", "--quiet"
      system bin/"agent-autosave", "init", "--agents", "generic", "--json"
      assert_path_exists testpath/"repo/.autosave/config.json"
      assert_match '"ok": true', shell_output("#{bin}/agent-autosave status --json")
      system bin/"agent-autosave", "remove", "--all", "--json"
    end
  end
end
