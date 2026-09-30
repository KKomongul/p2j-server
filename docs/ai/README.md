# AI 파이프라인

담당: 박영준. 현재 구현은 `app/services/ai/`에 있다.

## 처리 순서

1. 규칙 파서가 입력을 나누고 날짜·시간대·소요 시간·관련 목표를 추출한다.
2. Gemini가 원문과 규칙 초안을 대조해 어색한 제목과 명백한 해석 오류를 검수한다.
3. 앱의 “이해한 게 맞나요?” 화면에서 선택·수정 후 저장한다.

음성은 모바일의 `speech_to_text`로 문자화한다. 녹음 종료 버튼은 정리 단계로
바로 이동한다. 서버 `/v1/ai/parse`는 초안만 반환하며, 확인 뒤 `/v1/todos/bulk`가 저장한다.
완료 체크는 저장 후 별도로 수행한다.

## 설정

Git에서 제외된 서버 `.env`에 설정한다. 키를 앱이나 웹 빌드에 넣지 않는다.

```dotenv
GEMINI_API_KEY=본인_API_키
GEMINI_MODEL=gemini-2.5-flash-lite
```

모델 설정은 생략 가능하다. `.env`를 바꾼 뒤 서버를 재시작한다.
현재 검수는 Gemini만 사용하며 기존 `OPENAI_API_KEY`는 사용하지 않는다.
Google로 전달되는 데이터는 입력 원문, 규칙 초안, 기준 날짜, 관련 목표 정보다.
API 키는 인증 헤더로 전달하고 원문·키·응답 본문은 애플리케이션 로그에 남기지 않는다.

## 실패 처리와 응답

- 검수 성공: `parse_method=llm`. 기존 모바일 응답 형식은 유지한다.
- 키 없음, API 오류, 비정상 JSON, 안전 필터 차단, 8초 시간 초과: 규칙 초안을 유지한다.
- 규칙으로 나눌 수 없으면 원문 한 건을 초안으로 사용한다 (`parse_method=none`).
- 일일 한도 초과: Gemini를 건너뛰고 규칙 초안과 `quota_exceeded` 경고를 반환한다.
- 분당 한도 초과: 기존 `429 AI_QUOTA_EXCEEDED` 정책을 유지한다.
- 파서 자체의 내부 오류는 `503 AI_UNAVAILABLE`로 처리한다.

Gemini 구조화 응답은 Pydantic으로 검증한다. 잘못된 날짜는 기준일로 보정하며,
알 수 없는 목표 ID와 범위 밖 소요 시간은 제거한다. LLM의 의미 해석은 항상
정확하지 않으므로 사용자 확인을 거쳐 저장한다.

## 검증

`tests/test_ai_gemini.py`는 규칙 초안 전달, HTTP/네트워크 실패, 잘못된 응답,
쿼터 초과 시 호출 생략과 결과 보정을 검증한다. 테스트는 실제 키를 사용하지 않는다.
API 계약 테스트는 `tests/test_ai_api.py`, 규칙 파서는 `tests/test_ai_rules.py`에 있다.

공식 API 참고: https://ai.google.dev/api/generate-content
