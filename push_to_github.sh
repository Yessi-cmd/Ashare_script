#!/bin/bash
# GitHub 推送脚本

echo "📦 准备推送到 GitHub..."

# 1. 配置 git 用户（如果还没配置）
echo ""
echo "1️⃣ 配置 Git 用户信息"
read -p "请输入你的 GitHub 用户名: " github_username
read -p "请输入你的邮箱: " github_email

git config --global user.name "$github_username"
git config --global user.email "$github_email"
echo "✅ Git 用户配置完成"

# 2. 提交代码
echo ""
echo "2️⃣ 提交代码到本地仓库"
git commit -m "Initial commit: A股行情监控 V2 - 智能评分 + 持仓管理 + Telegram Bot"
echo "✅ 本地提交完成"

# 3. 创建 main 分支
echo ""
echo "3️⃣ 切换到 main 分支"
git branch -M main
echo "✅ 已切换到 main 分支"

# 4. 提示创建 GitHub 仓库
echo ""
echo "4️⃣ 创建 GitHub 仓库"
echo "请在浏览器中访问："
echo "   https://github.com/new"
echo ""
echo "创建仓库时："
echo "   - Repository name: Ashare_script （或你喜欢的名字）"
echo "   - Public/Private: 选 Public"
echo "   - 不要勾选 'Add a README'"
echo "   - 点击 'Create repository'"
echo ""
read -p "创建完成后，输入仓库名（直接回车默认用 Ashare_script）: " repo_name
repo_name=${repo_name:-Ashare_script}

# 5. 添加远程仓库并推送
echo ""
echo "5️⃣ 推送到 GitHub"
git remote add origin git@github.com:$github_username/$repo_name.git
git push -u origin main

echo ""
echo "🎉 推送成功！"
echo "仓库地址: https://github.com/$github_username/$repo_name"
