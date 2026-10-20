package com.order.common;

/**
 * 业务异常。由全局异常处理器转换为 { code, message } 响应。
 */
public class BizException extends RuntimeException {

    private final int code;

    public BizException(String message) {
        this(40001, message);
    }

    public BizException(int code, String message) {
        super(message);
        this.code = code;
    }

    public int getCode() {
        return code;
    }
}
