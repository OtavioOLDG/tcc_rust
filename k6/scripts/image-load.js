import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:3000';
const VUS = Number.parseInt(__ENV.VUS || '10', 10);
const DURATION = __ENV.DURATION || '5m';
const image = open('../fixtures/benchmark.jpeg', 'b');

export const options = {
  vus: VUS,
  duration: DURATION,

  summaryTrendStats: [
    'avg',
    'min',
    'med',
    'max',
    'p(90)',
    'p(95)',
    'p(99)',
  ],

  thresholds: {
    http_req_failed: ['rate==0'],
  },
};

export default function () {
  const response = http.post(`${BASE_URL}/image/grayscale`, image, {
    headers: {
      'Content-Type': 'image/jpeg',
    },
  });

  check(response, {
    'status 200': (r) => r.status === 200,
    'Content-Type image/png': (r) =>
      r.headers['Content-Type']?.includes('image/png'),
  });
}