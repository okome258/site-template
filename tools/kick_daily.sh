#!/bin/bash
# bot-server(Oracle)の cron から毎朝実行する。
# kick ブランチに push → kick.yml が main の「毎日のサイト更新」を起動する。
# (GitHub の定時実行が数時間遅れる対策。トークン不要、サーバーの site-template 専用デプロイキー ~/.ssh/site_template_kick を使う。~/.ssh/config に Host github-site-template)
set -e
D="$HOME/kick-site-template"
if [ ! -d "$D/.git" ]; then
  git clone -q --depth 1 -b kick git@github-site-template:okome258/site-template.git "$D"
fi
cd "$D"
git fetch -q --depth 1 origin kick
git reset -q --hard origin/kick
git -c user.name=okome258 -c user.email=okome258@users.noreply.github.com \
  commit -q --amend --allow-empty -m "kick $(TZ=Asia/Tokyo date '+%F %T')"
git push -q -f origin kick
echo "$(TZ=Asia/Tokyo date '+%F %T') kick OK"
