package com.order.service;

import com.order.common.BizException;
import com.order.dto.CreateOrderRequest;
import com.order.dto.UpdateOrderRequest;
import com.order.entity.Order;
import com.order.entity.OrderStatus;
import com.order.repository.OrderRepository;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * 订单业务。
 *
 * 状态流转集中在这一层：每个动作都先校验前置状态，不满足直接抛业务异常，
 * 校验通过后才写入新状态。这样可以保证状态机只有 5 条合法边。
 */
@Service
public class OrderServiceImpl {

    private final OrderRepository orderRepository;

    public OrderServiceImpl(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    /** 创建订单：初始状态为待支付。 */
    public Order create(CreateOrderRequest request) {
        Order order = new Order();
        order.setOrderNo("ORD" + System.currentTimeMillis());
        order.setReceiverName(request.getReceiverName());
        order.setPhone(request.getPhone());
        order.setAddress(request.getAddress());
        order.setAmount(request.getAmount());
        order.setRemark(request.getRemark() == null ? "" : request.getRemark());
        order.setStatus(OrderStatus.CREATED);
        return orderRepository.save(order);
    }

    public Order getById(Long id) {
        return orderRepository.findById(id)
                .orElseThrow(() -> new BizException(40401, "订单不存在"));
    }

    /** 分页查询，支持状态筛选与订单号/收货人关键字。 */
    public Map<String, Object> list(int page, int pageSize, String status, String keyword) {
        List<Order> all = orderRepository.findAll();
        List<Order> filtered = new ArrayList<>();
        for (Order order : all) {
            if (status != null && !status.isBlank() && !status.equalsIgnoreCase(order.getStatus().name())) {
                continue;
            }
            if (keyword != null && !keyword.isBlank()) {
                String kw = keyword.trim().toLowerCase(Locale.ROOT);
                boolean hit = order.getOrderNo().toLowerCase(Locale.ROOT).contains(kw)
                        || order.getReceiverName().toLowerCase(Locale.ROOT).contains(kw);
                if (!hit) {
                    continue;
                }
            }
            filtered.add(order);
        }
        int total = filtered.size();
        int from = Math.max(0, (page - 1) * pageSize);
        int to = Math.min(total, from + pageSize);
        List<Order> items = from >= total ? List.of() : filtered.subList(from, to);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("total", total);
        result.put("page", page);
        result.put("pageSize", pageSize);
        result.put("items", items);
        return result;
    }

    /** 修改收货信息与备注。状态不通过此接口修改。 */
    public Order update(Long id, UpdateOrderRequest request) {
        Order order = getById(id);
        if (order.getStatus() == OrderStatus.COMPLETED || order.getStatus() == OrderStatus.CANCELLED) {
            throw new BizException("已完成或已取消的订单不能修改");
        }
        order.setReceiverName(request.getReceiverName());
        order.setPhone(request.getPhone());
        order.setAddress(request.getAddress());
        order.setRemark(request.getRemark() == null ? "" : request.getRemark());
        return orderRepository.save(order);
    }

    public void delete(Long id) {
        getById(id);
        orderRepository.deleteById(id);
    }

    /** 支付：待支付 -> 已支付。 */
    public Order pay(Long id) {
        Order order = getById(id);
        if (!"CREATED".equals(order.getStatus().name())) {
            throw new BizException("当前状态不可支付，只有待支付订单可以支付");
        }
        order.setStatus(OrderStatus.PAID);
        return orderRepository.save(order);
    }

    /** 发货：已支付 -> 已发货。 */
    public Order ship(Long id) {
        Order order = getById(id);
        if (!"PAID".equals(order.getStatus().name())) {
            throw new BizException("当前状态不可发货，只有已支付订单可以发货");
        }
        order.setStatus(OrderStatus.SHIPPED);
        return orderRepository.save(order);
    }

    /** 完成：已发货 -> 已完成。 */
    public Order complete(Long id) {
        Order order = getById(id);
        if (!"SHIPPED".equals(order.getStatus().name())) {
            throw new BizException("当前状态不可完成，只有已发货订单可以完成");
        }
        order.setStatus(OrderStatus.COMPLETED);
        return orderRepository.save(order);
    }

    /** 取消：只有待支付订单可以取消（已支付走退款）。 */
    public Order cancel(Long id) {
        Order order = getById(id);
        if (!"CREATED".equals(order.getStatus().name())) {
            throw new BizException("当前状态不可取消，只有待支付订单可以取消");
        }
        order.setStatus(OrderStatus.CANCELLED);
        return orderRepository.save(order);
    }

    /** 退款：只有已支付订单可以退款。 */
    public Order refund(Long id) {
        Order order = getById(id);
        if (!"PAID".equals(order.getStatus().name())) {
            throw new BizException("当前状态不可退款，只有已支付订单可以退款");
        }
        order.setStatus(OrderStatus.REFUNDED);
        return orderRepository.save(order);
    }

    /** 恢复演示数据。 */
    public void reset() {
        orderRepository.resetToSeed();
    }
}
