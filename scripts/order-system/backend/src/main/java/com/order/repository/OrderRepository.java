package com.order.repository;

import com.order.entity.Order;
import com.order.entity.OrderStatus;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 订单内存存储。
 *
 * 说明：本项目作为自动化测试的被测系统，刻意不引入数据库，
 * 启动时写入 4 条种子数据（覆盖四种状态），保证每个状态流转用例都有可用起点。
 */
@Repository
public class OrderRepository {

    private final Map<Long, Order> store = new ConcurrentHashMap<>();

    private final AtomicLong idGenerator = new AtomicLong(1000);

    @PostConstruct
    public void init() {
        resetToSeed();
    }

    /** 恢复演示数据（供自动化测试重置环境使用）。 */
    public synchronized void resetToSeed() {
        store.clear();
        idGenerator.set(1000);
        saveSeed("ORD20260101001", "张三", "13800000001", "北京市海淀区中关村大街1号", new BigDecimal("199.00"), "请尽快发货", OrderStatus.CREATED, 1);
        saveSeed("ORD20260101002", "李四", "13800000002", "上海市浦东新区世纪大道100号", new BigDecimal("88.50"), "", OrderStatus.PAID, 2);
        saveSeed("ORD20260101003", "王五", "13800000003", "广州市天河区体育西路50号", new BigDecimal("320.00"), "易碎品", OrderStatus.SHIPPED, 3);
        saveSeed("ORD20260101004", "赵六", "13800000004", "深圳市南山区科技园路8号", new BigDecimal("45.00"), "", OrderStatus.COMPLETED, 4);
    }

    private void saveSeed(String orderNo, String receiverName, String phone, String address,
                          BigDecimal amount, String remark, OrderStatus status, int minutesAgo) {
        Order order = new Order();
        order.setId(idGenerator.incrementAndGet());
        order.setOrderNo(orderNo);
        order.setReceiverName(receiverName);
        order.setPhone(phone);
        order.setAddress(address);
        order.setAmount(amount);
        order.setRemark(remark);
        order.setStatus(status);
        order.setCreateTime(LocalDateTime.now().minusMinutes(minutesAgo * 10L));
        order.setUpdateTime(order.getCreateTime());
        store.put(order.getId(), order);
    }

    public Order save(Order order) {
        if (order.getId() == null) {
            order.setId(idGenerator.incrementAndGet());
            order.setCreateTime(LocalDateTime.now());
        }
        order.setUpdateTime(LocalDateTime.now());
        store.put(order.getId(), order);
        return order;
    }

    public Optional<Order> findById(Long id) {
        return Optional.ofNullable(store.get(id));
    }

    /** 按创建时间倒序返回全部订单（新建的排在最前），时间相同时用 id 倒序兜底。 */
    public List<Order> findAll() {
        List<Order> orders = new ArrayList<>(store.values());
        orders.sort(Comparator.comparing(Order::getCreateTime).reversed()
                .thenComparing(Comparator.comparing(Order::getId).reversed()));
        return orders;
    }

    public void deleteById(Long id) {
        store.remove(id);
    }

    public int count() {
        return store.size();
    }
}
