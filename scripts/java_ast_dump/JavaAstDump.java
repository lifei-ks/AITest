import com.sun.source.tree.*;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.Trees;

import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;
import java.io.File;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

/**
 * 用 JDK Compiler API 解析 Java 源码，输出结构化 AST 摘要。
 *
 * 只做语法解析，不做类型解析，因此不依赖项目 classpath。
 * 输出 JSON 供 Python 侧映射为 Endpoint / Entity / EnumDef / BusinessRule。
 */
public class JavaAstDump {
    private final Trees trees;
    private final SourcePositions positions;
    private final CompilationUnitTree unit;
    private final Map<Tree, Boolean> seen = new IdentityHashMap<>();

    private JavaAstDump(Trees trees, SourcePositions positions, CompilationUnitTree unit) {
        this.trees = trees;
        this.positions = positions;
        this.unit = unit;
    }

    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8));
        if (args.length == 0) {
            System.err.println("usage: JavaAstDump <file.java>...");
            System.exit(2);
        }
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            System.err.println("JDK compiler not available");
            System.exit(3);
        }
        List<File> files = new ArrayList<>();
        for (String arg : args) {
            files.add(new File(arg));
        }
        StandardJavaFileManager fm = compiler.getStandardFileManager(null, null, StandardCharsets.UTF_8);
        Iterable<? extends JavaFileObject> units = fm.getJavaFileObjectsFromFiles(files);
        JavacTask task = (JavacTask) compiler.getTask(
                null, fm, null, List.of("-proc:none"), null, units);
        Trees trees = Trees.instance(task);
        SourcePositions positions = trees.getSourcePositions();

        StringBuilder out = new StringBuilder();
        out.append("{\"units\":[");
        boolean firstUnit = true;
        for (CompilationUnitTree unit : task.parse()) {
            if (!firstUnit) out.append(",");
            firstUnit = false;
            JavaAstDump dumper = new JavaAstDump(trees, positions, unit);
            out.append(dumper.dumpUnit());
        }
        out.append("]}");
        System.out.println(out);
        fm.close();
    }

    private String dumpUnit() {
        String file = new File(unit.getSourceFile().toUri()).getPath().replace("\\", "/");
        List<String> types = new ArrayList<>();
        for (Tree decl : unit.getTypeDecls()) {
            if (decl instanceof ClassTree) {
                types.add(dumpClass((ClassTree) decl));
            }
        }
        return "{\"file\":" + quote(file) + ",\"types\":[" + String.join(",", types) + "]}";
    }

    private String dumpClass(ClassTree node) {
        List<String> annotations = dumpAnnotations(node.getModifiers());
        List<String> fields = new ArrayList<>();
        List<String> methods = new ArrayList<>();
        List<String> enumValues = new ArrayList<>();

        if (node.getKind() == Tree.Kind.ENUM) {
            for (Tree member : node.getMembers()) {
                if (member instanceof VariableTree) {
                    VariableTree v = (VariableTree) member;
                    if (v.getType() != null && v.getType().toString().equals(node.getSimpleName().toString())) {
                        enumValues.add("{\"name\":" + quote(v.getName().toString())
                                + ",\"args\":[" + exprList(v.getInitializer()) + "]}");
                    }
                }
            }
        } else {
            for (Tree member : node.getMembers()) {
                if (member instanceof VariableTree) {
                    fields.add(dumpField((VariableTree) member));
                } else if (member instanceof MethodTree) {
                    methods.add(dumpMethod((MethodTree) member));
                }
            }
        }

        return "{\"name\":" + quote(node.getSimpleName().toString())
                + ",\"kind\":" + quote(node.getKind().name())
                + ",\"line\":" + line(node)
                + ",\"annotations\":[" + String.join(",", annotations) + "]"
                + ",\"fields\":[" + String.join(",", fields) + "]"
                + ",\"methods\":[" + String.join(",", methods) + "]"
                + ",\"enumValues\":[" + String.join(",", enumValues) + "]}";
    }

    private String dumpField(VariableTree node) {
        return "{\"name\":" + quote(node.getName().toString())
                + ",\"type\":" + quote(node.getType() == null ? "" : node.getType().toString())
                + ",\"line\":" + line(node)
                + ",\"annotations\":[" + String.join(",", dumpAnnotations(node.getModifiers())) + "]}";
    }

    private String dumpMethod(MethodTree node) {
        List<String> params = new ArrayList<>();
        for (VariableTree p : node.getParameters()) {
            params.add("{\"name\":" + quote(p.getName().toString())
                    + ",\"type\":" + quote(p.getType() == null ? "" : p.getType().toString())
                    + ",\"line\":" + line(p)
                    + ",\"annotations\":[" + String.join(",", dumpAnnotations(p.getModifiers())) + "]}");
        }
        List<String> calls = new ArrayList<>();
        List<String> ifs = new ArrayList<>();
        if (node.getBody() != null) {
            collect(node.getBody(), calls, ifs);
        }
        return "{\"name\":" + quote(node.getName().toString())
                + ",\"returnType\":" + quote(node.getReturnType() == null ? "" : node.getReturnType().toString())
                + ",\"line\":" + line(node)
                + ",\"annotations\":[" + String.join(",", dumpAnnotations(node.getModifiers())) + "]"
                + ",\"params\":[" + String.join(",", params) + "]"
                + ",\"calls\":[" + String.join(",", calls) + "]"
                + ",\"ifs\":[" + String.join(",", ifs) + "]}";
    }

    private void collect(Tree tree, List<String> calls, List<String> ifs) {
        if (tree == null || seen.put(tree, Boolean.TRUE) != null) {
            return;
        }
        if (tree instanceof MethodInvocationTree) {
            MethodInvocationTree call = (MethodInvocationTree) tree;
            String receiver = "";
            String method = expr(call.getMethodSelect());
            if (call.getMethodSelect() instanceof MemberSelectTree) {
                MemberSelectTree ms = (MemberSelectTree) call.getMethodSelect();
                receiver = expr(ms.getExpression());
                method = ms.getIdentifier().toString();
            }
            List<String> args = new ArrayList<>();
            for (ExpressionTree arg : call.getArguments()) {
                args.add(quote(expr(arg)));
            }
            calls.add("{\"receiver\":" + quote(receiver)
                    + ",\"method\":" + quote(method)
                    + ",\"args\":[" + String.join(",", args) + "]"
                    + ",\"line\":" + line(call) + "}");
            // orElseThrow(() -> new XxxException("消息")) 这类断言不在 if 里，
            // 用外层调用表达式当守卫，否则整条业务规则会漏掉
            List<String> lambdaThrows = new ArrayList<>();
            for (ExpressionTree arg : call.getArguments()) {
                collectLambdaThrows(arg, lambdaThrows);
            }
            if (!lambdaThrows.isEmpty()) {
                ifs.add("{\"condition\":" + quote(expr(call))
                        + ",\"throws\":[" + String.join(",", lambdaThrows) + "]"
                        + ",\"line\":" + line(call)
                        + ",\"kind\":\"invocation\"}");
            }
        }
        if (tree instanceof IfTree) {
            IfTree it = (IfTree) tree;
            List<String> throwsList = new ArrayList<>();
            collectThrows(it.getThenStatement(), throwsList);
            collectThrows(it.getElseStatement(), throwsList);
            ifs.add("{\"condition\":" + quote(expr(it.getCondition()))
                    + ",\"throws\":[" + String.join(",", throwsList) + "]"
                    + ",\"line\":" + line(it) + "}");
        }
        for (Tree child : children(tree)) {
            collect(child, calls, ifs);
        }
    }

    private void collectThrows(Tree tree, List<String> out) {
        if (tree == null) return;
        if (tree instanceof ThrowTree) {
            ExpressionTree expr = ((ThrowTree) tree).getExpression();
            if (expr instanceof NewClassTree) {
                out.add(quote(messageOf((NewClassTree) expr)));
            }
        }
        for (Tree child : children(tree)) {
            collectThrows(child, out);
        }
    }

    /**
     * orElseThrow(() -> new BizException("x")) 的 lambda 体是 new，不是 throw；
     * 只认类名以 Exception/Error 结尾的构造，避免把 map(x -> new Foo()) 误判成业务规则。
     */
    private void collectLambdaThrows(ExpressionTree arg, List<String> out) {
        if (!(arg instanceof LambdaExpressionTree)) return;
        Tree body = ((LambdaExpressionTree) arg).getBody();
        if (body instanceof NewClassTree) {
            String message = exceptionMessageOf((NewClassTree) body);
            if (message != null) {
                out.add(quote(message));
            }
            return;
        }
        if (body instanceof BlockTree) {
            // 块里被 if 包住的 throw 已由 collect() 的 IfTree 分支产出，这里只取裸 throw 防重复
            for (StatementTree statement : ((BlockTree) body).getStatements()) {
                if (statement instanceof ThrowTree) {
                    ExpressionTree expr = ((ThrowTree) statement).getExpression();
                    if (expr instanceof NewClassTree) {
                        out.add(quote(messageOf((NewClassTree) expr)));
                    }
                }
            }
            return;
        }
        collectThrows(body, out);
    }

    private String exceptionMessageOf(NewClassTree node) {
        String type = expr(node.getIdentifier());
        String simple = type.substring(type.lastIndexOf('.') + 1);
        if (!simple.endsWith("Exception") && !simple.endsWith("Error")) {
            return null;
        }
        return messageOf(node);
    }

    private String messageOf(NewClassTree node) {
        for (ExpressionTree arg : node.getArguments()) {
            if (arg instanceof LiteralTree) {
                Object value = ((LiteralTree) arg).getValue();
                if (value instanceof String) {
                    return (String) value;
                }
            }
        }
        return "";
    }

    private List<? extends Tree> children(Tree tree) {
        List<Tree> out = new ArrayList<>();
        if (tree instanceof BlockTree) {
            out.addAll(((BlockTree) tree).getStatements());
        } else if (tree instanceof ExpressionStatementTree) {
            out.add(((ExpressionStatementTree) tree).getExpression());
        } else if (tree instanceof ReturnTree) {
            if (((ReturnTree) tree).getExpression() != null) {
                out.add(((ReturnTree) tree).getExpression());
            }
        } else if (tree instanceof VariableTree) {
            if (((VariableTree) tree).getInitializer() != null) {
                out.add(((VariableTree) tree).getInitializer());
            }
        } else if (tree instanceof AssignmentTree) {
            out.add(((AssignmentTree) tree).getVariable());
            out.add(((AssignmentTree) tree).getExpression());
        } else if (tree instanceof MethodInvocationTree) {
            out.add(((MethodInvocationTree) tree).getMethodSelect());
            out.addAll(((MethodInvocationTree) tree).getArguments());
        } else if (tree instanceof NewClassTree) {
            out.addAll(((NewClassTree) tree).getArguments());
        } else if (tree instanceof MemberSelectTree) {
            out.add(((MemberSelectTree) tree).getExpression());
        } else if (tree instanceof BinaryTree) {
            out.add(((BinaryTree) tree).getLeftOperand());
            out.add(((BinaryTree) tree).getRightOperand());
        } else if (tree instanceof UnaryTree) {
            out.add(((UnaryTree) tree).getExpression());
        } else if (tree instanceof ParenthesizedTree) {
            out.add(((ParenthesizedTree) tree).getExpression());
        } else if (tree instanceof ConditionalExpressionTree) {
            out.add(((ConditionalExpressionTree) tree).getCondition());
            out.add(((ConditionalExpressionTree) tree).getTrueExpression());
            out.add(((ConditionalExpressionTree) tree).getFalseExpression());
        } else if (tree instanceof LambdaExpressionTree) {
            out.add(((LambdaExpressionTree) tree).getBody());
        } else if (tree instanceof AssertTree) {
            out.add(((AssertTree) tree).getCondition());
            if (((AssertTree) tree).getDetail() != null) {
                out.add(((AssertTree) tree).getDetail());
            }
        } else if (tree instanceof IfTree) {
            out.add(((IfTree) tree).getCondition());
            out.add(((IfTree) tree).getThenStatement());
            if (((IfTree) tree).getElseStatement() != null) {
                out.add(((IfTree) tree).getElseStatement());
            }
        } else if (tree instanceof TryTree) {
            out.add(((TryTree) tree).getBlock());
            out.addAll(((TryTree) tree).getCatches());
        } else if (tree instanceof CatchTree) {
            out.add(((CatchTree) tree).getBlock());
        } else if (tree instanceof ForLoopTree) {
            out.add(((ForLoopTree) tree).getStatement());
        } else if (tree instanceof EnhancedForLoopTree) {
            out.add(((EnhancedForLoopTree) tree).getStatement());
        } else if (tree instanceof WhileLoopTree) {
            out.add(((WhileLoopTree) tree).getStatement());
        } else if (tree instanceof DoWhileLoopTree) {
            out.add(((DoWhileLoopTree) tree).getStatement());
        } else if (tree instanceof SwitchTree) {
            out.addAll(((SwitchTree) tree).getCases());
        } else if (tree instanceof CaseTree) {
            out.addAll(((CaseTree) tree).getStatements());
        } else if (tree instanceof SynchronizedTree) {
            out.add(((SynchronizedTree) tree).getBlock());
        } else if (tree instanceof LabeledStatementTree) {
            out.add(((LabeledStatementTree) tree).getStatement());
        }
        return out;
    }

    private List<String> dumpAnnotations(ModifiersTree modifiers) {
        List<String> out = new ArrayList<>();
        for (AnnotationTree annotation : modifiers.getAnnotations()) {
            List<String> args = new ArrayList<>();
            for (ExpressionTree arg : annotation.getArguments()) {
                args.add(quote(expr(arg)));
            }
            out.add("{\"name\":" + quote(expr(annotation.getAnnotationType()))
                    + ",\"args\":[" + String.join(",", args) + "]}");
        }
        return out;
    }

    private String exprList(ExpressionTree tree) {
        return tree == null ? "" : quote(expr(tree));
    }

    private String expr(Tree tree) {
        if (tree == null) return "";
        if (tree instanceof LiteralTree) {
            Object value = ((LiteralTree) tree).getValue();
            return value == null ? "null" : String.valueOf(value);
        }
        if (tree instanceof IdentifierTree) {
            return ((IdentifierTree) tree).getName().toString();
        }
        if (tree instanceof MemberSelectTree) {
            MemberSelectTree ms = (MemberSelectTree) tree;
            return expr(ms.getExpression()) + "." + ms.getIdentifier();
        }
        if (tree instanceof MethodInvocationTree) {
            MethodInvocationTree call = (MethodInvocationTree) tree;
            List<String> args = new ArrayList<>();
            for (ExpressionTree arg : call.getArguments()) {
                args.add(expr(arg));
            }
            return expr(call.getMethodSelect()) + "(" + String.join(", ", args) + ")";
        }
        if (tree instanceof AssignmentTree) {
            AssignmentTree a = (AssignmentTree) tree;
            return expr(a.getVariable()) + " = " + expr(a.getExpression());
        }
        if (tree instanceof BinaryTree) {
            BinaryTree b = (BinaryTree) tree;
            return expr(b.getLeftOperand()) + " " + operator(b.getKind()) + " " + expr(b.getRightOperand());
        }
        if (tree instanceof UnaryTree) {
            UnaryTree u = (UnaryTree) tree;
            String op = u.getKind() == Tree.Kind.LOGICAL_COMPLEMENT ? "!" : u.getKind().name();
            return op + expr(u.getExpression());
        }
        if (tree instanceof ParenthesizedTree) {
            return "(" + expr(((ParenthesizedTree) tree).getExpression()) + ")";
        }
        if (tree instanceof NewClassTree) {
            NewClassTree n = (NewClassTree) tree;
            List<String> args = new ArrayList<>();
            for (ExpressionTree arg : n.getArguments()) {
                args.add(expr(arg));
            }
            return "new " + expr(n.getIdentifier()) + "(" + String.join(", ", args) + ")";
        }
        if (tree instanceof LambdaExpressionTree) {
            LambdaExpressionTree lambda = (LambdaExpressionTree) tree;
            List<String> names = new ArrayList<>();
            for (VariableTree p : lambda.getParameters()) {
                names.add(p.getName().toString());
            }
            String head = names.size() == 1 ? names.get(0) : "(" + String.join(", ", names) + ")";
            return head + " -> " + expr(lambda.getBody());
        }
        return unescapeUnicode(tree.toString().replace("\n", " ").replace("\r", " "));
    }

    /** javac 的 toString() 会把中文字面量转成 \\uXXXX，输出前还原成可读文本。 */
    private String unescapeUnicode(String text) {
        StringBuilder sb = new StringBuilder(text.length());
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '\\' && i + 5 < text.length() && text.charAt(i + 1) == 'u') {
                String hex = text.substring(i + 2, i + 6);
                try {
                    sb.append((char) Integer.parseInt(hex, 16));
                    i += 5;
                    continue;
                } catch (NumberFormatException ignored) {
                    // 不是合法转义，按原样保留
                }
            }
            sb.append(c);
        }
        return sb.toString();
    }

    private String operator(Tree.Kind kind) {
        switch (kind) {
            case PLUS: return "+";
            case MINUS: return "-";
            case MULTIPLY: return "*";
            case DIVIDE: return "/";
            case REMAINDER: return "%";
            case LESS_THAN: return "<";
            case GREATER_THAN: return ">";
            case LESS_THAN_EQUAL: return "<=";
            case GREATER_THAN_EQUAL: return ">=";
            case EQUAL_TO: return "==";
            case NOT_EQUAL_TO: return "!=";
            case CONDITIONAL_AND: return "&&";
            case CONDITIONAL_OR: return "||";
            default: return kind.name();
        }
    }

    private long line(Tree tree) {
        long pos = positions.getStartPosition(unit, tree);
        return pos < 0 ? 0 : unit.getLineMap().getLineNumber(pos);
    }

    private static String quote(String value) {
        if (value == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.append("\"").toString();
    }
}
