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
