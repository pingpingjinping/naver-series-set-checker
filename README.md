# Naver Series → Discord Watch

네이버 시리즈의 아래 검색결과를 매일 확인해서, 새 작품이 생겼을 때만 Discord Webhook으로 알려줍니다.

- 검색어: `세트`
- 분류: 만화
- 정렬: 업데이트순
- 확인 범위: 앞 3페이지
- 최초 실행: 현재 목록을 기준점으로만 저장하고 알림은 보내지 않음
- 이후 실행: 새 `productNo`만 Discord로 알림

## 1. 새 GitHub 저장소 만들기

Private 저장소여도 됩니다.

이 압축파일의 내용을 저장소 루트에 그대로 올리세요.

## 2. Discord Webhook 만들기

Discord에서 알림을 받을 채널의 설정으로 들어가 Webhook을 하나 만듭니다.

Webhook URL 전체를 복사합니다.

## 3. GitHub Secret 등록

저장소:

Settings → Secrets and variables → Actions → New repository secret

Name:

DISCORD_WEBHOOK

Secret:

Discord에서 복사한 Webhook URL

## 4. Actions 권한 확인

저장소:

Settings → Actions → General → Workflow permissions

`Read and write permissions`를 선택합니다.

workflow에도 `permissions: contents: write`가 들어 있습니다.

## 5. 첫 실행

Actions → `Naver Series Set Watch` → Run workflow

첫 실행은 현재 검색결과를 `state.json`에 기록하기만 하고 Discord 알림은 보내지 않습니다.

그 다음부터 새 작품이 발견되면 Discord로 전송됩니다.

## 자동 실행 시간

현재 `.github/workflows/check.yml` 설정:

한국시간 매일 오전 9시

GitHub Actions cron은 정확히 09:00:00에 시작된다는 보장은 없고 몇 분 정도 늦어질 수 있습니다.

## 체크 시간을 바꾸려면

`.github/workflows/check.yml`의 cron을 수정하세요.

예:
- 한국시간 매일 오전 9시: `0 0 * * *`
- 한국시간 매일 정오: `0 3 * * *`
- 한국시간 매일 오후 6시: `0 9 * * *`

## 수동 테스트

Actions 화면에서 `Run workflow`를 누르면 언제든 즉시 확인할 수 있습니다.

---

## 가격 변동 체크 상품 추가/삭제

가격을 추적하고 싶은 상품은 저장소 루트의 `price_urls.txt`에 **상품 URL을 한 줄에 하나씩** 넣으면 됩니다.

예:

```text
https://store.onstove.com/ko/games/4067
https://store.steampowered.com/app/42670/Singularity/
https://store.steampowered.com/bundle/13066/Atelier_Dusk_Trilogy_Deluxe_Pack/
```

현재 전용 파서가 있는 주소:

- STOVE 게임/컬렉션: `https://store.onstove.com/.../games/상품번호`
- Steam 일반 게임: `https://store.steampowered.com/app/앱번호/...`
- Steam 번들: `https://store.steampowered.com/bundle/번들번호/...`

빈 줄과 `#`으로 시작하는 줄은 무시합니다.

상품을 빼고 싶으면 `price_urls.txt`에서 해당 URL 한 줄만 삭제하면 됩니다. `price_state.json`은 실행할 때 자동으로 갱신되므로 직접 수정할 필요 없습니다.

새 URL을 처음 추가한 실행에서는 현재 가격을 **기준점으로만 저장하고 Discord 알림은 보내지 않습니다.** 이후 가격, 할인율, 세일 시작/종료, 할인 종료 시각 등이 바뀌면 Discord로 알림이 옵니다.

Steam 번들과 STOVE 컬렉션처럼 기본적으로 항상 적용되는 할인은 기간 세일과 별도로 구분합니다. 따라서 상시 번들/컬렉션 할인만 있는 상태를 세일 시작으로 오판하지 않습니다.

수동으로 바로 확인하려면 WalnutPi에서:

```bash
~/walnutpi_dispatch.sh price
```

또는 GitHub Actions의 `WalnutPi Price Watch` 워크플로를 직접 실행하면 됩니다.


---

## Discord 슬래시 명령으로 가격 관리

WalnutPi에 작은 Discord 봇을 상시 실행해 두면 Discord에서 GitHub Actions를 리모컨처럼 호출할 수 있습니다.
실제 가격 조회/상태 저장은 기존 GitHub Actions가 담당하고, Pi의 봇은 명령 수신과 workflow_dispatch 호출만 수행합니다.

지원 명령:

- `/가격` → `price.yml`을 강제 실행해서 지금 가격 확인
- `/목록` → `check.yml`을 실행하고 현재 가격 목록을 Discord로 전송
- `/추가 url:<URL>` → 가격 추적 URL 추가
- `/삭제 numbers:1,3` → 현재 목록 번호 기준 삭제

명령은 `DISCORD_ALLOWED_USER_ID`에 지정한 사용자 한 명만 실행할 수 있습니다.
일반 Discord 메시지 내용은 읽지 않으므로 Message Content Intent는 필요하지 않습니다.

### 1. Discord Bot 만들기

Discord Developer Portal에서 Application/Bot을 만든 뒤 서버에 초대합니다.
슬래시 명령을 사용할 수 있도록 `applications.commands` scope를 포함하세요.

필요한 값:

- Bot Token → `DISCORD_BOT_TOKEN`
- 본인 Discord User ID → `DISCORD_ALLOWED_USER_ID`
- 봇을 넣은 서버 ID → `DISCORD_GUILD_ID` (권장)

`DISCORD_GUILD_ID`를 넣으면 해당 서버에 명령을 즉시 동기화합니다.
비워두면 글로벌 슬래시 명령으로 등록되어 Discord에 반영되기까지 시간이 걸릴 수 있습니다.

### 2. GitHub Token 만들기

Fine-grained Personal Access Token을 권장합니다.

- Repository access: `pingpingjinping/naver-series-set-checker`
- Repository permissions → Actions: `Read and write`

이 토큰은 봇이 workflow_dispatch를 호출하는 용도로만 사용합니다.
저장소 파일 수정은 기존 GitHub Actions의 `GITHUB_TOKEN`이 담당합니다.

### 3. WalnutPi 설치

저장소가 `/home/pi/.local/share/naver-series-set-checker`에 설치되어 있다고 가정합니다.

```bash
cd /home/pi/.local/share/naver-series-set-checker
git pull

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-discord-bot.txt

mkdir -p /home/pi/.config/naver-series-set-checker
cp discord-price-bot.env.example \
  /home/pi/.config/naver-series-set-checker/discord-price-bot.env
chmod 600 /home/pi/.config/naver-series-set-checker/discord-price-bot.env
```

그 다음 `discord-price-bot.env`에 Discord 값을 입력합니다.

기존 WalnutPi GitHub 토큰이 `/home/pi/.config/naver-series-set-checker/github_token`에 있으면
`GITHUB_TOKEN`을 다시 복사할 필요 없이 그대로 재사용합니다. 다른 경로를 쓸 때만
`GITHUB_TOKEN_FILE`을 변경하세요.

서비스 설치:

```bash
sudo cp systemd/walnutpi-discord-price-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now walnutpi-discord-price-bot.service
```

상태/로그 확인:

```bash
systemctl status walnutpi-discord-price-bot.service
journalctl -u walnutpi-discord-price-bot.service -f
```

봇은 가격 크롤링 자체를 하지 않고 Discord Gateway 연결과 GitHub API 호출만 담당합니다.
