# ruff: noqa: E501
"""Embedded analytics SDK for embedding Cryo widgets in external apps.

Allows customers to embed Cryo dashboards and widgets into their
own applications using JavaScript SDK + API key authentication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.model_utils import qualified_table_name
from cryo.db.models import AuditLog, Table, TableStats


@dataclass
class WidgetConfig:
    """Configuration for an embeddable widget."""

    widget_type: str  # "table-health-card", "maintenance-status", "cost-trend"
    table_id: UUID
    api_key: str
    style: dict[str, str] | None = None


def generate_embed_code(
    widget_config: WidgetConfig,
    cryo_base_url: str = "https://cryo.yourcompany.com",
) -> str:
    """
    Generate HTML/JS embed code for a widget.

    Args:
        widget_config: Widget configuration
        cryo_base_url: Base URL of Cryo instance

    Returns:
        HTML embed code
    """
    container_id = f"cryo-widget-{widget_config.table_id}"

    code = f"""<!-- Cryo Embedded Widget -->
<script src="{cryo_base_url}/embed.js"></script>
<div id="{container_id}"></div>
<script>
  Cryo.embed({{
    containerId: '{container_id}',
    apiKey: '{widget_config.api_key}',
    widget: '{widget_config.widget_type}',
    tableId: '{widget_config.table_id}',
    style: {widget_config.style or {}}
  }});
</script>"""

    return code


def get_widget_data(
    session: Session,
    widget_type: str,
    table_id: UUID,
) -> dict[str, Any]:
    """
    Get data for an embeddable widget.

    This would be called by the Cryo API when the embed.js makes requests.

    Args:
        session: Database session
        widget_type: Widget type
        table_id: Table ID

    Returns:
        Widget data as JSON-serializable dict
    """
    table = session.get(Table, table_id)
    if not table:
        return {"error": "Table not found"}

    stats = session.get(TableStats, table_id)
    if not stats:
        return {"error": "No stats available"}

    table_name = qualified_table_name(table)

    if widget_type == "table-health-card":
        last_optimized_at = session.scalar(
            select(AuditLog.ts)
            .where(
                AuditLog.table_id == table_id,
                AuditLog.status == "succeeded",
            )
            .order_by(AuditLog.ts.desc())
            .limit(1)
        )
        return {
            "table_name": table_name,
            "grade": stats.grade,
            "score": stats.overall_score,
            "total_size_gb": stats.total_size_gb,
            "small_files_count": stats.small_files_count,
            "last_optimized_at": last_optimized_at.isoformat() if last_optimized_at else None,
            "estimated_monthly_cost_usd": stats.estimated_monthly_s3_cost_usd,
        }

    elif widget_type == "maintenance-status":
        # Get recent maintenance jobs
        stmt = (
            select(AuditLog)
            .where(AuditLog.table_id == table_id)
            .order_by(AuditLog.ts.desc())
            .limit(5)
        )

        recent_jobs = session.execute(stmt).scalars().all()

        return {
            "table_name": table_name,
            "recent_jobs": [
                {
                    "action": job.action,
                    "status": job.status,
                    "started_at": job.ts.isoformat(),
                    "duration_seconds": (
                        job.duration_ms / 1000 if job.duration_ms is not None else None
                    ),
                }
                for job in recent_jobs
            ],
        }

    elif widget_type == "cost-trend":
        # Would fetch cost history from TableStatsHistory
        return {
            "table_name": table_name,
            "current_cost_usd": stats.estimated_monthly_s3_cost_usd,
            "trend": "stable",  # Simplified
        }

    return {"error": f"Unknown widget type: {widget_type}"}


# JavaScript SDK code (would be served as /embed.js):
EMBED_JS_TEMPLATE = """
(function(window) {
  'use strict';
  
  var Cryo = {
    baseUrl: '{base_url}',
    
    embed: function(config) {
      var container = document.getElementById(config.containerId);
      if (!container) {
        console.error('Cryo: Container not found:', config.containerId);
        return;
      }
      
      // Fetch widget data
      fetch(this.baseUrl + '/api/embed/widget/' + config.widget, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Cryo-API-Key': config.apiKey
        },
        body: JSON.stringify({
          table_id: config.tableId
        })
      })
      .then(function(response) {
        if (!response.ok) {
          throw new Error('Failed to load widget');
        }
        return response.json();
      })
      .then(function(data) {
        Cryo.renderWidget(container, config.widget, data, config.style);
      })
      .catch(function(error) {
        console.error('Cryo:', error);
        container.innerHTML = '<div style="color: red;">Failed to load Cryo widget</div>';
      });
    },
    
    renderWidget: function(container, widgetType, data, style) {
      if (widgetType === 'table-health-card') {
        this.renderHealthCard(container, data, style);
      } else if (widgetType === 'maintenance-status') {
        this.renderMaintenanceStatus(container, data, style);
      } else if (widgetType === 'cost-trend') {
        this.renderCostTrend(container, data, style);
      }
    },
    
    renderHealthCard: function(container, data, style) {
      var gradeColor = {
        'A': '#52c41a',
        'B': '#73d13d',
        'C': '#faad14',
        'D': '#fa8c16',
        'F': '#f5222d'
      }[data.grade] || '#999';
      
      container.innerHTML = `
        <div class="cryo-health-card" style="border: 2px solid $${gradeColor}; border-radius: 8px; padding: 16px; font-family: sans-serif;">
          <h3 style="margin: 0 0 12px 0;">${data.table_name}</h3>
          <div style="display: flex; gap: 16px;">
            <div style="flex: 1;">
              <div style="font-size: 48px; font-weight: bold; color: $${gradeColor};">${data.grade}</div>
              <div style="font-size: 12px; color: #666;">Health Grade</div>
            </div>
            <div style="flex: 2; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 14px;">
              <div><strong>Size:</strong> ${data.total_size_gb.toFixed(1)} GB</div>
              <div><strong>Score:</strong> ${data.score.toFixed(1)}/100</div>
              <div><strong>Small Files:</strong> ${data.small_files_count.toLocaleString()}</div>
              <div><strong>Cost:</strong> $${data.estimated_monthly_cost_usd.toFixed(2)}/mo</div>
            </div>
          </div>
        </div>
      `;
    },
    
    renderMaintenanceStatus: function(container, data, style) {
      var jobsHtml = data.recent_jobs.map(function(job) {
        var statusColor = job.status === 'completed' ? '#52c41a' : job.status === 'failed' ? '#f5222d' : '#faad14';
        return `
          <div style="padding: 8px; border-bottom: 1px solid #eee;">
            <span style="color: $${statusColor}; font-weight: bold;">${job.status.toUpperCase()}</span>
            <span style="margin-left: 8px;">${job.action}</span>
            <span style="float: right; font-size: 12px; color: #666;">${job.duration_seconds}s</span>
          </div>
        `;
      }).join('');
      
      container.innerHTML = `
        <div class="cryo-maintenance-status" style="border: 1px solid #ddd; border-radius: 8px; padding: 16px; font-family: sans-serif;">
          <h3 style="margin: 0 0 12px 0;">Maintenance History</h3>
          ${jobsHtml}
        </div>
      `;
    },
    
    renderCostTrend: function(container, data, style) {
      container.innerHTML = `
        <div class="cryo-cost-trend" style="border: 1px solid #ddd; border-radius: 8px; padding: 16px; font-family: sans-serif;">
          <h3 style="margin: 0 0 12px 0;">Monthly Cost</h3>
          <div style="font-size: 32px; font-weight: bold;">$${data.current_cost_usd.toFixed(2)}</div>
          <div style="font-size: 14px; color: #666;">Trend: ${data.trend}</div>
        </div>
      `;
    }
  };
  
  window.Cryo = Cryo;
})(window);
"""
