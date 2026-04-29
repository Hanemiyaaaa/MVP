from fastapi import FastAPI, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from .database import engine, Base, SessionLocal
from .models import File as FileModel, DataRow, Correction, CorrectionLog
import pandas as pd
import datetime
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI()
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "MVP is running"}


# ЗАГРУЗКА CSV
@app.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    business_date: str = Form(...),
    user_name: str = Form(...),
    db: Session = Depends(get_db)
):
    # Парсим бизнес-дату
    try:
        business_date_parsed = datetime.datetime.strptime(
            business_date, "%Y-%m-%d"
        ).date()
    except ValueError:
        return {"error": "Неверный формат даты. Используй YYYY-MM-DD"}

    # Автоматическая дата загрузки
    upload_date = datetime.datetime.now()

    # Создаём запись файла
    file_record = FileModel(
        filename=file.filename,
        business_date=business_date_parsed,
        user_name=user_name,
        upload_date=upload_date
    )

    db.add(file_record)
    db.commit()
    db.refresh(file_record)

    # Чтение CSV чанками для больших файлов
    chunk_size = 100_000

    try:
        for chunk in pd.read_csv(file.file, chunksize=chunk_size):

            # Проверка колонок
            required_columns = [
                "account_id",
                "client_type",
                "product_type",
                "balance",
                "currency",
                "risk_flag"
            ]

            if not all(col in chunk.columns for col in required_columns):
                return {
                    "error": f"Неверный формат CSV. Ожидаются колонки: {required_columns}"
                }

            # Подготовка bulk insert
            rows_to_add = [
                DataRow(
                    file_id=file_record.id,
                    account_id=str(row["account_id"]),
                    client_type=str(row["client_type"]),
                    product_type=str(row["product_type"]),
                    balance=str(row["balance"]),
                    currency=str(row["currency"]),
                    risk_flag=str(row["risk_flag"]),
                    version="original"
                )
                for _, row in chunk.iterrows()
            ]

            db.bulk_save_objects(rows_to_add)
            db.commit()

    except Exception as e:
        return {"error": f"Ошибка чтения или записи CSV: {str(e)}"}

    file.file.close()

    return {
        "message": "File uploaded successfully",
        "file_id": file_record.id
    }


# СПИСОК ВСЕХ ФАЙЛОВ
@app.get("/files")
def get_files(db: Session = Depends(get_db)):
    files = db.query(FileModel).all()

    return [
        {
            "id": f.id,
            "filename": f.filename,
            "business_date": f.business_date,
            "user_name": f.user_name,
            "upload_date": f.upload_date
        }
        for f in files
    ]


# ФИЛЬТР ФАЙЛОВ
@app.get("/files/filter")
def filter_files(
    business_date: str = None,
    user_name: str = None,
    upload_date: str = None,
    db: Session = Depends(get_db)
):
    query = db.query(FileModel)

    if business_date:
        try:
            date_parsed = datetime.datetime.strptime(
                business_date, "%Y-%m-%d"
            ).date()
            query = query.filter(FileModel.business_date == date_parsed)
        except:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid business_date format"}
            )

    if user_name:
        query = query.filter(FileModel.user_name == user_name)

    if upload_date:
        try:
            date_parsed = datetime.datetime.strptime(
                upload_date, "%Y-%m-%d"
            ).date()
            query = query.filter(FileModel.upload_date == date_parsed)
        except:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid upload_date format"}
            )

    files = query.all()

    return [
        {
            "id": f.id,
            "filename": f.filename,
            "business_date": f.business_date,
            "user_name": f.user_name,
            "upload_date": f.upload_date
        }
        for f in files
    ]


@app.get("/data/{file_id}/{version}")
def get_data(
    file_id: int,
    version: str,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    if version not in ["original", "corrected"]:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid version"}
        )

    # 👉 если просим corrected — проверим есть ли он вообще
    if version == "corrected":
        exists = db.query(DataRow).filter(
            DataRow.file_id == file_id,
            DataRow.version == "corrected"
        ).first()

        # ❗ если нет corrected — откатываемся на original
        if not exists:
            version = "original"

    rows = db.query(DataRow).filter(
        DataRow.file_id == file_id,
        DataRow.version == version
    ).order_by(DataRow.id).offset(offset).limit(limit).all()

    return [
        {
            "id": r.id,
            "account_id": r.account_id,
            "client_type": r.client_type,
            "product_type": r.product_type,
            "balance": r.balance,
            "currency": r.currency,
            "risk_flag": r.risk_flag,
        }
        for r in rows
    ]


# СОЗДАНИЕ КОРРЕКТИРОВОК
@app.post("/corrections")
def create_correction(
    file_id: int = Form(...),
    field_to_update: str = Form(...),
    new_value: str = Form(...),
    condition_field: str = Form(...),
    operator: str = Form(...),
    condition_value: str = Form(...),
    db: Session = Depends(get_db)
):
    correction = Correction(
        file_id=file_id,
        field_to_update=field_to_update,
        new_value=new_value,
        condition_field=condition_field,
        operator=operator,
        condition_value=condition_value
    )

    db.add(correction)
    db.commit()

    return {"message": "Correction created"}


# ПРИМЕНЕНИЕ КОРРЕКТИРОВОК
def check_condition(row, rule):
    value = str(row.get(rule.condition_field, ""))

    if rule.operator == "equals":
        return value == rule.condition_value

    if rule.operator == "contains":
        return rule.condition_value in value

    if rule.operator == "upper_equals":
        return value.upper() == rule.condition_value.upper()

    return False


@app.post("/apply-corrections/{file_id}")
def apply_corrections(
    file_id: int,
    applied_by: str = Form(...),
    db: Session = Depends(get_db)
):
    db.query(DataRow).filter(
        DataRow.file_id == file_id,
        DataRow.version == "corrected"
    ).delete(synchronize_session=False)

    db.commit()

    rows = db.query(DataRow).filter(
        DataRow.file_id == file_id,
        DataRow.version == "original"
    ).all()

    rules = db.query(Correction).filter(
        Correction.file_id == file_id
    ).all()

    updated_count = 0
    rows_to_add = []

    for row in rows:
        row_dict = {
            "account_id": row.account_id,
            "client_type": row.client_type,
            "product_type": row.product_type,
            "balance": row.balance,
            "currency": row.currency,
            "risk_flag": row.risk_flag
        }

        new_data = row_dict.copy()
        row_updated = False

        for rule in rules:
            if check_condition(row_dict, rule):
                new_data[rule.field_to_update] = rule.new_value
                row_updated = True

                log = CorrectionLog(
                    correction_id=rule.id,
                    file_id=file_id,
                    applied_by=applied_by
                )
                db.add(log)

        if row_updated:
            updated_count += 1

        rows_to_add.append(
            DataRow(
                file_id=file_id,
                account_id=new_data["account_id"],
                client_type=new_data["client_type"],
                product_type=new_data["product_type"],
                balance=new_data["balance"],
                currency=new_data["currency"],
                risk_flag=new_data["risk_flag"],
                version="corrected"
            )
        )

    if rows_to_add:
        db.bulk_save_objects(rows_to_add)
        db.commit()

    return {
        "message": "Corrections applied",
        "updated_rows": updated_count
    }


# UI
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/ui")
def get_ui():
    return FileResponse("app/static/index.html")