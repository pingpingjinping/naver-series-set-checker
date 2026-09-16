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
