import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 15_000,
  headers: {
    Accept: 'application/json',
  },
})
