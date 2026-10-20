package com.order.entity;

/**
 * 订单状态。
 *
 * 合法流转（共 5 条）：
 *   待支付 CREATED   -> 已支付 PAID       （支付）
 *   待支付 CREATED   -> 已取消 CANCELLED   （取消未支付订单）
 *   已支付 PAID      -> 已发货 SHIPPED     （发货）
 *   已支付 PAID      -> 已退款 REFUNDED    （退款）
 *   已发货 SHIPPED   -> 已完成 COMPLETED   （完成）
 *
 * 终态：已完成 COMPLETED、已取消 CANCELLED、已退款 REFUNDED
 */
public enum OrderStatus {

    CREATED("待支付"),
    PAID("已支付"),
    SHIPPED("已发货"),
    COMPLETED("已完成"),
    CANCELLED("已取消"),
    REFUNDED("已退款");

    private final String label;

    OrderStatus(String label) {
        this.label = label;
    }

    public String getLabel() {
        return label;
    }
}
