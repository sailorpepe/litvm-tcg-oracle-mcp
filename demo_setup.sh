#!/bin/zsh
# Run BEFORE recording — hides all personal info

# Clean prompt for ZSH — just a dollar sign
export PS1="$ "

# Force window + tab title
print -Pn "\e]0;LitVM TCG Oracle\a"

# No mail warnings
export MAILCHECK=0

clear

echo "⛓️  LitVM TCG Oracle — MCP Server"
echo "   pip install litvm-tcg-oracle"
echo ""
