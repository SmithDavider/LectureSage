package com.keji.classroomnote.ui.mindmap

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import com.keji.classroomnote.net.MindMapNode
import kotlin.math.abs

class MindMapView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {
    private var nodes: List<MindMapNode> = emptyList()
    private var selectedId: String? = null
    private var layouts: List<LayoutNode> = emptyList()
    private var dragOffset = 0f
    private var dragStartY = 0f
    private var dragActive = false

    var onSelectionChanged: ((MindMapNode?) -> Unit)? = null
    var onDragMove: ((nodeId: String, direction: Int) -> Unit)? = null

    private val backgroundPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
    }
    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#0F766E")
        style = Paint.Style.STROKE
        strokeWidth = 2f
    }
    private val selectedPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#CCFBF1")
    }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#94A3B8")
        strokeWidth = 2f
    }
    private val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#0F172A")
        textSize = 34f
    }
    private val emptyPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#64748B")
        textSize = 36f
        textAlign = Paint.Align.CENTER
    }

    fun setNodes(value: List<MindMapNode>) {
        nodes = value
        selectedId = findNode(value, selectedId)?.nodeId
        rebuild()
        invalidate()
    }

    fun selectedNode(): MindMapNode? = findNode(nodes, selectedId)

    fun nodes(): List<MindMapNode> = nodes

    fun select(nodeId: String) {
        selectedId = nodeId
        onSelectionChanged?.invoke(selectedNode())
        invalidate()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        rebuild()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (layouts.isEmpty()) {
            canvas.drawText("点击“添加子节点”创建导图", width / 2f, height / 2f, emptyPaint)
            return
        }

        layouts.forEach { item ->
            item.childrenIds.forEach { childId ->
                val child = layouts.firstOrNull { it.node.nodeId == childId }
                if (child != null) {
                    val startX = item.x + item.width
                    val endX = child.x
                    val startY = item.y + ROW_HEIGHT / 2f
                    val endY = child.y + ROW_HEIGHT / 2f
                    val middleX = (startX + endX) / 2f
                    canvas.drawLine(startX, startY, middleX, startY, linePaint)
                    canvas.drawLine(middleX, startY, middleX, endY, linePaint)
                    canvas.drawLine(middleX, endY, endX, endY, linePaint)
                }
            }

            val drawY = if (item.node.nodeId == selectedId) item.y + dragOffset else item.y
            val rect = RectF(item.x, drawY, item.x + item.width, drawY + ROW_HEIGHT - 10f)
            canvas.drawRoundRect(rect, 14f, 14f, backgroundPaint)
            canvas.drawRoundRect(rect, 14f, 14f, borderPaint)
            if (item.node.nodeId == selectedId) canvas.drawRoundRect(rect, 14f, 14f, selectedPaint)
            drawText(item, rect, canvas)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                val hit = hitNode(event.x, event.y)
                selectedId = hit?.node?.nodeId
                onSelectionChanged?.invoke(selectedNode())
                dragActive = hit != null
                dragStartY = event.y
                dragOffset = 0f
                invalidate()
                performClick()
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                if (dragActive) dragOffset = event.y - dragStartY
                invalidate()
                return true
            }
            MotionEvent.ACTION_UP -> {
                if (dragActive && abs(dragOffset) > ROW_HEIGHT / 2f) {
                    val direction = if (dragOffset < 0) -1 else 1
                    selectedId?.let { id -> onDragMove?.invoke(id, direction) }
                }
                dragActive = false
                dragOffset = 0f
                invalidate()
                performClick()
                return true
            }
            MotionEvent.ACTION_CANCEL -> {
                dragActive = false
                dragOffset = 0f
                invalidate()
                return true
            }
        }
        return super.performClick()
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    private fun drawText(item: LayoutNode, rect: RectF, canvas: Canvas) {
        val textWidth = (rect.width() - 20f).toInt().coerceAtLeast(1)
        val layout = StaticLayout.Builder
            .obtain(item.node.text, 0, item.node.text.length, textPaint, textWidth)
            .setAlignment(Layout.Alignment.ALIGN_CENTER)
            .setLineSpacing(0f, 1.0f)
            .setIncludePad(false)
            .build()
        val textY = rect.top + (rect.height() - layout.height) / 2f
        canvas.withTranslation(rect.left + 10f, textY) { layout.draw(this) }
    }

    private inline fun Canvas.withTranslation(
        x: Float,
        y: Float,
        block: Canvas.() -> Unit,
    ) {
        val checkpoint = save()
        translate(x, y)
        block()
        restoreToCount(checkpoint)
    }

    private fun rebuild() {
        val result = mutableListOf<LayoutNode>()
        flatten(nodes, depth = 0, result = result)
        layouts = result
    }

    private fun flatten(
        values: List<MindMapNode>,
        depth: Int,
        result: MutableList<LayoutNode>,
    ) {
        values.forEach { node ->
            val x = MARGIN + depth * LEVEL_WIDTH
            val measured = textPaint.measureText(node.text)
            val width = measured.coerceIn(MIN_WIDTH, MAX_WIDTH)
            result.add(
                LayoutNode(
                    node = node,
                    depth = depth,
                    x = x,
                    y = result.size * ROW_HEIGHT + MARGIN,
                    width = width,
                    childrenIds = node.children.map { it.nodeId },
                ),
            )
            flatten(node.children, depth + 1, result)
        }
    }

    private fun hitNode(x: Float, y: Float): LayoutNode? {
        return layouts.lastOrNull { item ->
            x >= item.x && x <= item.x + item.width &&
                y >= item.y && y <= item.y + ROW_HEIGHT
        }
    }

    private data class LayoutNode(
        val node: MindMapNode,
        val depth: Int,
        val x: Float,
        val y: Float,
        val width: Float,
        val childrenIds: List<String>,
    )

    private companion object {
        private const val MARGIN = 18f
        private const val ROW_HEIGHT = 66f
        private const val LEVEL_WIDTH = 38f
        private const val MIN_WIDTH = 140f
        private const val MAX_WIDTH = 240f
    }
}

private fun findNode(nodes: List<MindMapNode>, id: String?): MindMapNode? {
    if (id == null) return null
    nodes.forEach { node ->
        if (node.nodeId == id) return node
        findNode(node.children, id)?.let { return it }
    }
    return null
}
