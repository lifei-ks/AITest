import request from './request'

export function fetchOrders(params) {
  return request.get('/orders', { params })
}

export function fetchOrder(id) {
  return request.get(`/orders/${id}`)
}

export function createOrder(data) {
  return request.post('/orders', data)
}

export function updateOrder(id, data) {
  return request.put(`/orders/${id}`, data)
}

export function deleteOrder(id) {
  return request.delete(`/orders/${id}`)
}

export function payOrder(id) {
  return request.post(`/orders/${id}/pay`)
}

export function shipOrder(id) {
  return request.post(`/orders/${id}/ship`)
}

export function completeOrder(id) {
  return request.post(`/orders/${id}/complete`)
}

export function cancelOrder(id) {
  return request.post(`/orders/${id}/cancel`)
}

export function refundOrder(id) {
  return request.post(`/orders/${id}/refund`)
}

export function fetchStatusOptions() {
  return request.get('/orders/status-options')
}
