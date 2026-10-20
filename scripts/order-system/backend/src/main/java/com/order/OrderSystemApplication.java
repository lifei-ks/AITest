package com.order;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 订单管理系统 —— AICase 被测靶站。
 *
 * 设计说明：
 * 1. 使用内存存储，不依赖任何外部数据库，启动即可用；
 * 2. 内置 4 条种子订单，覆盖「待支付 / 已支付 / 已发货 / 已完成」四种状态；
 * 3. 订单状态流转严格校验前置状态，非法流转抛业务异常。
 */
@SpringBootApplication
public class OrderSystemApplication {

    public static void main(String[] args) {
        SpringApplication.run(OrderSystemApplication.class, args);
    }
}
