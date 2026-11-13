import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:3000';
const VUS = Number.parseInt(__ENV.VUS || '10', 10);
const DURATION = __ENV.DURATION || '60s';

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_failed: ['rate==0'],
  },
};

export default function () {
  const response = http.get(`${BASE_URL}/health`);

  check(response, {
    'status 200': (r) => r.status === 200,
    'Content-Type application/json': (r) =>
      r.headers['Content-Type']?.includes('application/json'),
    'body correto': (r) => r.body === '{"status":"ok"}',
  });
}