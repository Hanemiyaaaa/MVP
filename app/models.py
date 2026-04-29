from sqlalchemy import Column, Integer, String, Date, ForeignKey, DateTime
from .database import Base
import datetime


# Таблица файлов
class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    business_date = Column(Date)
    upload_date = Column(Date, default=datetime.date.today)
    user_name = Column(String)


# Таблица с данными (две версии: original и corrected)
class DataRow(Base):
    __tablename__ = "data_table"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id"))

    account_id = Column(String)
    client_type = Column(String)
    product_type = Column(String)
    balance = Column(String)
    currency = Column(String)
    risk_flag = Column(String)
    version = Column(String, default="original")  # original / corrected


# Таблица правил корректировок
class Correction(Base):
    __tablename__ = "corrections"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer)

    field_to_update = Column(String)
    new_value = Column(String)

    condition_field = Column(String)
    operator = Column(String)
    condition_value = Column(String)


# Лог применения корректировок
class CorrectionLog(Base):
    __tablename__ = "correction_logs"

    id = Column(Integer, primary_key=True, index=True)
    correction_id = Column(Integer)
    file_id = Column(Integer)

    applied_by = Column(String)
    applied_at = Column(DateTime, default=datetime.datetime.utcnow)