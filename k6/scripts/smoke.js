import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:3000';

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    http_req_failed: ['rate==0'],
  },
};

export default function () {
  const response = http.get(`${BASE_URL}/health`);

  check(response, {
    'status 200': (r) => r.status === 200,
    'body correto': (r) => r.body === '{"status":"ok"}',
  });
}
