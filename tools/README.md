# 바탕화면 P2J 실행 환경

작업 기준은 `C:\Users\Algorithm\Desktop\P2j`이다.

- 서버: `p2j-server`, `DB-AI(PYJ)` 브랜치, ae9614e.
- 모바일: `p2j-mobile`, main, ff0465a. 원격의 6개 커밋을 fast-forward로 반영했다.
- 기존 앱·서버 소스는 수정하지 않았다. 이 tools 폴더만 새로 추가했다.
- 이전 Documents 복제본의 수정은 옮기지 않았다. 이전 서버도 종료했다.

## 실행

서버 저장소 루트에서 터미널 두 개로 실행한다. 이미 백그라운드 실행 중이면 아래 종료 명령부터 사용한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/server.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/web.ps1
```

- PC 웹 화면: http://127.0.0.1:8080
- API 문서: http://127.0.0.1:8001/v1/docs
- 웹·API 연결: http://127.0.0.1:8080/v1/health
- Flutter SDK: `%LOCALAPPDATA%\p2j-tools\flutter`
- 개발 DB는 PostgreSQL `p2j_desktop`, Redis DB 1. 이전 `p2j` DB와 분리했다.
- `.env`와 가상환경, 로그, 생성 코드, 웹 빌드는 Git 제외다.

서버 코드는 저장하면 자동 재시작한다. 모바일 코드는 아래로 다시 빌드한 후 브라우저를 새로고침한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/web.ps1 -BuildWeb
```

이 명령은 빌드 후 웹 서버도 시작하므로 기존 웹 프로세스는 먼저 종료한다.
현재 웹은 USE_MOCK=false, API_BASE_URL=/v1이며 별도 프록시를 통해 실제 서버를 호출한다.

## 검증 결과

2026-09-30: 서버 pytest 170개와 Flutter 테스트 54개 통과. Flutter 테스트는 CONTRACT_BASE_URL=http://127.0.0.1:8001/v1로 실제 서버에 연결했다. 웹 릴리스 빌드와 브라우저 인트로 표시, DB·Redis 헬스체크도 성공했다.

마이그레이션 0003까지 적용했다. `alembic check`는 기존 모델 12개 PK의 Identity 선언 불일치로 실패한다. 기존 소스 보존 요청에 따라 이번에는 수정하지 않았다. 실행 성공과 스키마 일치 검증은 별개다.

## 아이폰

현재는 PC의 loopback에만 열려 있어 아이폰에서 이 주소로 접속할 수 없다. 같은 네트워크를 사용할 수 없으므로 임시 HTTPS 터널이 필요하다. 아직 외부 주소를 열지 않았다.

Safari 웹은 네이티브 iOS 앱이 아니다. 토큰 저장을 위해 HTTPS가 필요하며, 실제 아이폰의 음성·사진 권한과 동작은 별도 확인해야 한다. iOS 네이티브 빌드에는 Mac/Xcode 환경이 필요하다.

## 백그라운드 종료

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/stop.ps1
```

이 도구로 시작한 기존 백그라운드 서버·웹 PID만 검증 후 종료한다. 터미널에서 직접 실행한 프로세스는 Ctrl+C로 종료한다. 컨테이너 종료는 `docker compose stop`이다. 볼륨을 삭제하지 않는다.
