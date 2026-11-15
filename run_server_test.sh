#!/bin/bash
# يشغّل الخدمات الوهمية + السيرفر على بورتات الاختبار
cd "$(dirname "$0")/.."
export FSF_CONFIG=tests/config.test.json FSF_DB=/tmp/fsf_test.db FSF_DATA=/tmp/fsf_test_data FSF_NO_BROWSER=1
rm -rf /tmp/fsf_test.db /tmp/fsf_test_data
python3 tests/mock_services.py &
sleep 1
python3 server/main.py
