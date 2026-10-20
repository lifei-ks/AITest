package com.example.order;

public class OrderServiceImpl {

    public Order create(OrderDTO dto) {
        Order order = new Order();
        order.setStatus(OrderStatus.CREATED);
        return order;
    }

    public void pay(String orderId) {
        Order order = find(orderId);
        if (!"CREATED".equals(order.getStatus())) {
            throw new BizException("当前状态不可支付");
        }
        order.setStatus(OrderStatus.PAID);
    }

    public void ship(String orderId) {
        Order order = find(orderId);
        if (!"PAID".equals(order.getStatus())) {
            throw new BizException("当前状态不可发货");
        }
        order.setStatus(OrderStatus.SHIPPED);
    }

    public void complete(String orderId) {
        Order order = find(orderId);
        if (!"SHIPPED".equals(order.getStatus())) {
            throw new BizException("当前状态不可完成");
        }
        order.setStatus(OrderStatus.COMPLETED);
    }

    public void cancel(String orderId) {
        Order order = find(orderId);
        if ("SHIPPED".equals(order.getStatus()) || "COMPLETED".equals(order.getStatus())) {
            throw new BizException("当前状态不可取消");
        }
        order.setStatus(OrderStatus.CANCELLED);
    }
}
