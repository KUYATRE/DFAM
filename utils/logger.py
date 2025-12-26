import logging
import os
from datetime import datetime

def setup_logger(name="DFAM_Project"):
    """
    로그 설정을 초기화하고 로거 인스턴스를 반환합니다.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 이미 핸들러가 설정되어 있다면 중복 생성 방지
    if not logger.handlers:
        # 로그 포맷 설정 (시간 [로그레벨] 파일명:라인 - 메시지)
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s'
        )

        # 1. 콘솔 출력 핸들러
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 2. 파일 저장 핸들러
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        today = datetime.now().strftime("%Y-%m-%d")
        file_handler = logging.FileHandler(
            f"{log_dir}/app_{today}.log", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

# 전역에서 바로 사용할 수 있도록 인스턴스 생성
logger = setup_logger()